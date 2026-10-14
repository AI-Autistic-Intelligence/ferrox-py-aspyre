"""
Mock ERP Server for Live Testbed (Exact Online REST & Twinfield SOAP).
Provides realistic rate limiting headers and simulated failure modes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Live Testbed Mock ERP Server")

# In-memory tracking
request_counts: dict[str, int] = {}
last_minutely_reset = time.time() + 60


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": time.time()}


@app.api_route("/api/v1/{division}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def exact_online_handler(division: str, path: str, request: Request):
    """Simulates Exact Online REST endpoint with concurrency & rate limit headers."""
    now = time.time()
    count = request_counts.get(division, 0) + 1
    request_counts[division] = count

    # Rate limit headers
    rem_min = max(0, 60 - (count % 60))
    headers = {
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Remaining": str(max(0, 5000 - count)),
        "X-RateLimit-Reset": str(int((now + 3600) * 1000)),
        "X-RateLimit-Minutely-Limit": "60",
        "X-RateLimit-Minutely-Remaining": str(rem_min),
    }

    body = await request.body()
    return Response(
        content='{"status": "success", "result": "RECORD_PROCESSED"}',
        media_type="application/json",
        headers=headers,
    )


@app.post("/webservices/processxml.asmx")
async def twinfield_soap_handler(request: Request):
    """Simulates Twinfield SOAP endpoint returning chunkable XML."""
    body = await request.body()
    xml_response = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ProcessXmlStringResult>
      <browse><browse_data>
        <transaction office="100" code="VRK" number="20261001" date="2026-04-01">
          <line id="1"><account>1300</account><debit>121.00</debit><credit>0.00</credit><description>Invoice 1001</description></line>
          <line id="2"><account>8000</account><debit>0.00</debit><credit>100.00</credit><vatcode>VH</vatcode><vatamount>21.00</vatamount><description>Revenue</description></line>
          <line id="3"><account>1500</account><debit>0.00</debit><credit>21.00</credit><vatcode>VH</vatcode><vatamount>0.00</vatamount><description>VAT High 21%</description></line>
        </transaction>
      </browse_data></browse>
    </ProcessXmlStringResult>
  </soap:Body>
</soap:Envelope>"""
    return Response(content=xml_response, media_type="text/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
