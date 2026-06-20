"""Phyto packet extraction + debit-row matching.

Ports phyto_agent.py + match_debit_row.py. The Azure Document Intelligence
client and the LLM agent are built lazily (and the agent memoized) so importing
this module is cheap and the server boots without the AI stack installed.
"""

from typing import Optional

from pydantic import BaseModel

from . import config


class DebitRow(BaseModel):
    certificate_number: str
    date: Optional[str] = None
    debit_amount: Optional[float] = None


class PhytoExtraction(BaseModel):
    current_certificate_number: str
    replaced_certificate_number: str = ""
    order_number: Optional[int] = None
    debit_rows: list[DebitRow] = []


_INSTRUCTIONS = """
You extract structured data from a California Phytosanitary Certificate packet.
The packet contains: the certificate itself (page 1), an inspection log,
a Debit Transaction report, and possibly supporting docs (pick ticket, email).

── CERTIFICATE NUMBERS ─────────────────────────────────────────────
There are TWO distinct certificate numbers on the certificate page and
you MUST NOT confuse them:

1. current_certificate_number
   - The number printed in the "NO." field in the TOP-RIGHT header of
     page 1, to the right of the "PHYTOSANITARY CERTIFICATE" title block
     and to the left of the state seal. It is the ONLY certificate number
     in the header. Format example: "S-C-06019-14089360-CA".
   - This is the certificate being issued right now.

2. replaced_certificate_number
   - Appears INSIDE the "ADDITIONAL DECLARATION" free-text block near the
     bottom of page 1, e.g. "This certificate replaces and cancels
     <NUMBER>, issued on <date>, due to <reason>." It is the OLD
     certificate this new one supersedes. Any number preceded by
     "replaces", "cancels", "cancelled", "supersedes", or "amended from"
     is the REPLACED one, never the current one.
   - If there is no "replaces and cancels" (or equivalent) phrase, return
     "" for this field. Do NOT invent one and do NOT copy the
     current_certificate_number into it.

CRITICAL: The number in the "NO." header field is ALWAYS the current one.
The number inside the "replaces and cancels" sentence is ALWAYS the
replaced one. Never swap them. Never use a number from the Debit
Transaction table, the inspection log, the pick ticket, or the email as
the current_certificate_number.

── DEBIT ROWS ──────────────────────────────────────────────────────
Extract EVERY row from the "Debit Transaction" / "Report Detail" table
exactly as written. Include rows whose certificate number does not look
related — do not filter. For each row capture:
  - certificate_number  (verbatim, preserve dashes and case)
  - date                (verbatim string as shown)
  - debit_amount        (numeric, no $ sign; null if blank)

── ORDER NUMBER ────────────────────────────────────────────────────
Extract the order number from the "PICK TICKET" page if it exists. It is
a numeric field prefixed by "Order #" in the upper-left corner of the
pick ticket. If not present, return null.

── RULES ───────────────────────────────────────────────────────────
- Do NOT decide which debit row matches the certificate.
- Do NOT perform any matching logic.
- Do NOT normalize, reformat, or "clean up" certificate numbers.
- Return data exactly as written on the document.
"""

_agent = None


def _get_agent():
    """Build (once) the phyto extraction agent."""
    global _agent
    if _agent is None:
        from langchain.agents import create_agent

        _agent = create_agent(
            name="phyto_match_agent",
            model="openai:gpt-5.4",
            system_prompt=_INSTRUCTIONS,
            response_format=PhytoExtraction,
        )
    return _agent


async def convert_pdf_to_markdown(file_path: str) -> str:
    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import DocumentContentFormat
    from azure.core.credentials import AzureKeyCredential

    async with DocumentIntelligenceClient(
        endpoint=config.DOC_INTEL_ENDPOINT,
        credential=AzureKeyCredential(config.DOC_INTEL_KEY),
    ) as client:
        with open(file_path, "rb") as f:
            poller = await client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=f,
                output_content_format=DocumentContentFormat.MARKDOWN,
            )
        result = await poller.result()
    return result.content or ""


async def run_phyto_agent(file_path: str) -> PhytoExtraction:
    """Extract structured data from one phyto PDF packet."""
    document_text = await convert_pdf_to_markdown(file_path)
    result = await _get_agent().ainvoke(
        {"messages": [{"role": "user", "content": (
            "Extract structured data from this phytosanitary certificate packet.\n"
            "- current_certificate_number: ONLY the number in the 'NO.' header field "
            "at the top-right of the certificate page. Never use a number after "
            "'replaces', 'cancels', 'cancelled', or 'supersedes'.\n"
            "- replaced_certificate_number: the number in the 'ADDITIONAL DECLARATION' "
            "block after 'replaces and cancels' (or similar); '' if none.\n"
            "- debit_rows: every row from the Debit Transaction table, verbatim.\n"
            "- order_number: from the PICK TICKET page ('Order #'), else null.\n\n"
            "Do not choose the matching debit row. Do not perform any debit matching.\n\n"
            f"Perform the action on this document: {document_text}"
        )}]}
    )
    return result["structured_response"]


def match_debit_row(current_certificate_number: str, debit_rows: list[DebitRow]) -> dict:
    """Find the debit row whose certificate number matches the current one (normalized)."""
    def norm(value: str) -> str:
        return value.strip().replace(" ", "").replace("-", "").upper()

    target = norm(current_certificate_number)
    for row in debit_rows:
        if norm(row.certificate_number) == target:
            return {
                "matched": True,
                "current_certificate_number": current_certificate_number,
                "matched_certificate_number": row.certificate_number,
                "date": row.date,
                "debit_amount": row.debit_amount,
                "reason": "Exact certificate match found",
            }
    return {
        "matched": False,
        "current_certificate_number": current_certificate_number,
        "matched_certificate_number": None,
        "date": None,
        "debit_amount": None,
        "reason": "No Exact Match found",
    }
