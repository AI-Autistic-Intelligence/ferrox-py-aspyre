"""
XAF 3.2 (Auditfile Financieel) Exporter for Belastingdienst Tax Inspection Readiness.
Converts FerroX audit chain and journal entries into official Dutch Standard XML format.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Sequence
from ..dutch_sbr.models import JournalEntry
from .ledger_chain import AuditBlock


class XafAuditExporter:
    """
    Exports Dutch Belastingdienst-compliant XAF 3.2 XML files for external fiscal audits.
    """

    @staticmethod
    def export_xaf_xml(
        division_id: str,
        company_name: str,
        kvk_number: str,
        fiscal_year: int,
        entries: Sequence[JournalEntry],
        audit_blocks: Sequence[AuditBlock],
    ) -> str:
        """
        Builds compliant XAF 3.2 XML containing general ledger transactions and tamper-proof hash trail.
        """
        root = ET.Element("auditfile", attrib={
            "xmlns": "http://www.auditfiles.nl/XAF/3.2",
            "version": "3.2",
        })

        # Header
        header = ET.SubElement(root, "header")
        ET.SubElement(header, "fiscalYear").text = str(fiscal_year)
        ET.SubElement(header, "startDate").text = f"{fiscal_year}-01-01"
        ET.SubElement(header, "endDate").text = f"{fiscal_year}-12-31"
        ET.SubElement(header, "curCode").text = "EUR"
        ET.SubElement(header, "dateCreated").text = time.strftime("%Y-%m-%d")
        ET.SubElement(header, "softwareDesc").text = "Aspyre-Ferrox AI Autonomous Accounting Engine"
        ET.SubElement(header, "softwareVersion").text = "1.0.0"

        # Company
        company = ET.SubElement(root, "company")
        ET.SubElement(company, "companyName").text = company_name
        ET.SubElement(company, "companyIdent").text = division_id
        ET.SubElement(company, "kvkNumber").text = kvk_number

        # Transactions
        transactions_el = ET.SubElement(root, "transactions")
        for entry in entries:
            tx_el = ET.SubElement(transactions_el, "transaction")
            ET.SubElement(tx_el, "nr").text = entry.entry_number
            ET.SubElement(tx_el, "desc").text = f"Journal {entry.journal_code} #{entry.entry_number}"
            ET.SubElement(tx_el, "trDt").text = entry.entry_date
            ET.SubElement(tx_el, "periodNumber").text = str(entry.period)

            for line in entry.lines:
                line_el = ET.SubElement(tx_el, "trLine")
                ET.SubElement(line_el, "lineNr").text = str(line.line_number)
                ET.SubElement(line_el, "accID").text = line.account_code
                if line.rgs_code:
                    ET.SubElement(line_el, "rgsCode").text = line.rgs_code
                ET.SubElement(line_el, "desc").text = line.description
                if line.debit > 0:
                    ET.SubElement(line_el, "amt").text = f"{line.debit:.2f}"
                    ET.SubElement(line_el, "amtType").text = "D"
                else:
                    ET.SubElement(line_el, "amt").text = f"{line.credit:.2f}"
                    ET.SubElement(line_el, "amtType").text = "C"

                if line.vat_category:
                    vat_el = ET.SubElement(line_el, "vat")
                    ET.SubElement(vat_el, "vatCode").text = line.vat_category.value
                    ET.SubElement(vat_el, "vatAmt").text = f"{line.vat_amount:.2f}"

        # Embedded Cryptographic Hash-Chain Audit Trail for Inspector Verification
        audit_trail_el = ET.SubElement(root, "cryptographicAuditTrail")
        for b in audit_blocks:
            b_el = ET.SubElement(audit_trail_el, "block")
            ET.SubElement(b_el, "index").text = str(b.block_index)
            ET.SubElement(b_el, "timestamp").text = b.timestamp
            ET.SubElement(b_el, "prevHash").text = b.prev_hash
            ET.SubElement(b_el, "entityType").text = b.entity_type
            ET.SubElement(b_el, "entityId").text = b.entity_id
            ET.SubElement(b_el, "action").text = b.action
            ET.SubElement(b_el, "actor").text = b.actor
            ET.SubElement(b_el, "payloadDigest").text = b.payload_digest
            ET.SubElement(b_el, "blockHash").text = b.block_hash

        xml_bytes = ET.tostring(root, encoding="utf-8")
        parsed = minidom.parseString(xml_bytes)
        return parsed.toprettyxml(indent="  ")
