import re
from docx import Document
from dataclasses import dataclass
from typing import Optional


@dataclass
class FormattedRun:
    text: str
    bold: bool
    underline: bool


@dataclass
class ParsedOfficeAction:
    application_number: Optional[str]
    ir_number: Optional[str]          # International Registration number
    trademark_name: Optional[str]
    applicant_name: Optional[str]
    formatting_convention: Optional[str]
    full_text: str
    paragraphs_with_formatting: list[dict]


def _all_text_chunks(paragraphs: list[dict], tables) -> list[str]:
    """Yield every text chunk visible in the document (paragraphs + table cells)."""
    chunks = [p["text"] for p in paragraphs if p["text"].strip()]
    if tables:
        for table in tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                # Add each cell individually
                chunks.extend(c for c in cells if c)
                # Also add adjacent-cell pairs as "label: value" so a label in
                # one cell and a number in the next cell get matched together.
                for i in range(len(cells) - 1):
                    if cells[i] and cells[i + 1]:
                        chunks.append(f"{cells[i]}: {cells[i + 1]}")
    return chunks


def _clean_num(s: str) -> Optional[str]:
    n = re.sub(r"[\s,]", "", s)
    return n if re.fullmatch(r"\d{7,8}", n) else None


def _extract_numbers(paragraphs: list[dict], tables) -> tuple[Optional[str], Optional[str]]:
    """
    Return (cipo_app_number, ir_number).

    CIPO app number heuristics (in priority order):
      1. Value after a label containing "Our File", "Our Ref", "Notre dossier"
      2. Any 7-digit number starting with 2 (current CIPO app numbers are ~2,000,000–2,200,000)
      3. IR number used as fallback (can also be used to query CIPO)

    IR number: value after a label containing "IR No", "IR Number",
    "International Registration".
    """
    chunks = _all_text_chunks(paragraphs, tables)

    our_file_re = re.compile(
        r"(?:Our\s+(?:File|Ref(?:erence)?)|Notre\s+(?:dossier|r[ée]f(?:[ée]rence)?))"
        r"[^0-9]{0,20}?([\d][\d,\s]{4,9}[\d])",
        re.IGNORECASE,
    )
    ir_re = re.compile(
        r"(?:IR\s+(?:No\.?|Number|Num\.?)|"
        r"Int(?:ernational)?\s+Reg(?:istration)?\.?\s*(?:No\.?|Number)?)"
        r"[^0-9]{0,20}?([\d][\d,\s]{4,9}[\d])",
        re.IGNORECASE,
    )

    app_number: Optional[str] = None
    ir_number: Optional[str] = None

    # Pass 1 — labeled searches
    for text in chunks:
        if not app_number:
            m = our_file_re.search(text)
            if m:
                app_number = _clean_num(m.group(1))

        if not ir_number:
            m = ir_re.search(text)
            if m:
                ir_number = _clean_num(m.group(1))

        if app_number and ir_number:
            break

    # Pass 2 — any 7-digit number starting with 2 (CIPO app numbers ~2xxxxxx)
    if not app_number:
        for text in chunks:
            for m in re.finditer(r"\b(2\d{6})\b", text):
                candidate = m.group(1)
                if candidate != ir_number:   # don't re-use the IR number
                    app_number = candidate
                    break
            if app_number:
                break

    # Pass 3 — if still nothing, fall back to any 7-digit number
    if not app_number:
        for text in chunks:
            for m in re.finditer(r"\b(\d{7})\b", text):
                candidate = m.group(1)
                if candidate != ir_number:
                    app_number = candidate
                    break
            if app_number:
                break

    return app_number, ir_number


def _extract_re_table(doc) -> dict:
    """Extract trademark name and applicant from the RE: header table."""
    result = {"trademark_name": None, "applicant_name": None}
    for table in doc.tables:
        cells = [cell.text.strip() for row in table.rows for cell in row.cells if cell.text.strip()]
        if any(c in ("RE:", "Re:", "Trademark:", "Applicant:") for c in cells):
            for i, cell in enumerate(cells):
                if cell in ("Trademark:", "Marque de commerce:") and i + 1 < len(cells):
                    result["trademark_name"] = cells[i + 1].replace("\n", " ").strip()
                if cell in ("Applicant:", "Demandeur:") and i + 1 < len(cells):
                    result["applicant_name"] = cells[i + 1].replace("\n", " ").strip()
    return result


def extract_document_debug(file_path: str) -> dict:
    """Return raw text from paragraphs and tables for diagnosing extraction issues."""
    doc = Document(file_path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for t_idx, table in enumerate(doc.tables):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        tables.append({"table_index": t_idx, "rows": rows})
    return {"paragraphs": paras[:60], "tables": tables}


def parse_office_action(file_path: str) -> ParsedOfficeAction:
    doc = Document(file_path)
    paragraphs_with_formatting = []
    full_text_lines = []

    for para in doc.paragraphs:
        if not para.text.strip():
            continue

        tagged = ""
        runs = []
        for run in para.runs:
            t = run.text
            bold = bool(run.bold)
            underline = bool(run.underline)
            if bold and underline:
                t = f"[BOLD_UNDERLINE]{t}[/BOLD_UNDERLINE]"
            elif bold:
                t = f"[BOLD]{t}[/BOLD]"
            elif underline:
                t = f"[UNDERLINE]{t}[/UNDERLINE]"
            tagged += t
            runs.append({"text": run.text, "bold": bold, "underline": underline})

        paragraphs_with_formatting.append({
            "text": para.text,
            "tagged": tagged,
            "runs": runs,
        })
        full_text_lines.append(para.text)

    re_fields = _extract_re_table(doc)
    app_number, ir_number = _extract_numbers(paragraphs_with_formatting, doc.tables)

    return ParsedOfficeAction(
        application_number=app_number,
        ir_number=ir_number,
        trademark_name=re_fields["trademark_name"],
        applicant_name=re_fields["applicant_name"],
        formatting_convention=None,
        full_text="\n".join(full_text_lines),
        paragraphs_with_formatting=paragraphs_with_formatting,
    )
