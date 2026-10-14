"""
Twinfield SOAP Client with Stateful Session Injection and Parser Integration.
"""

from __future__ import annotations

import httpx
from typing import Any
from .session_pooler import TwinfieldSessionPooler, TwinfieldSession
from .streaming_parser import TwinfieldStreamingParser, TwinfieldTransaction


class TwinfieldSoapClient:
    def __init__(
        self,
        session_pooler: TwinfieldSessionPooler,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.session_pooler = session_pooler
        self.http_client = http_client or httpx.AsyncClient(timeout=30.0)

    def _build_soap_envelope(self, session: TwinfieldSession, inner_xml: str) -> str:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Header>
    <Header xmlns="http://www.twinfield.com/">
      <SessionID>{session.session_id}</SessionID>
    </Header>
  </soap:Header>
  <soap:Body>
    <ProcessXmlString xmlns="http://www.twinfield.com/">
      <xmlRequest><![CDATA[{inner_xml}]]></xmlRequest>
    </ProcessXmlString>
  </soap:Body>
</soap:Envelope>"""

    async def execute_query(
        self,
        office_code: str,
        user_code: str,
        password: str,
        inner_xml: str,
    ) -> list[TwinfieldTransaction]:
        """
        Executes an XML query using a pooled session and streams results asynchronously.
        """
        session = await self.session_pooler.get_valid_session(office_code, user_code, password)
        endpoint = f"{session.cluster_url}/webservices/processxml.asmx"
        soap_payload = self._build_soap_envelope(session, inner_xml)

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://www.twinfield.com/ProcessXmlString",
        }

        response = await self.http_client.post(endpoint, content=soap_payload, headers=headers)
        
        # Check if session was revoked on remote side
        if "Server.Authentication.SessionInvalid" in response.text or response.status_code == 401:
            await self.session_pooler.invalidate_session(office_code, user_code)
            # Re-authenticate once and retry
            session = await self.session_pooler.get_valid_session(office_code, user_code, password, force_refresh=True)
            soap_payload = self._build_soap_envelope(session, inner_xml)
            response = await self.http_client.post(endpoint, content=soap_payload, headers=headers)

        return await TwinfieldStreamingParser.parse_all(response.content)
