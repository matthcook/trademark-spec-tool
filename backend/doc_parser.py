import re
from docx import Document
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FormattedRun:
    text: str
    bold: bool
    underline: bool


@dataclass
class ParsedOfficeAction:
    application_number: Optional[str]
    trademark_name: Optional[str]
    applicant_name: Optional[str]
    formatting_convention: Optional[str]
    full_text: str
    paragraphs_with_formatting: list[dict]


# Label patterns that identify an application number cell
_APP_LABEL = re.compile(
    r'(?:Application\s+(?:No\.?|Number|Num\.?|#|no\b)|'
    r'App\.?\s+No\.?|'
    r'No\.?\s*de\s*(?:la\s*)?demande)',
    re.IGNORECASE
)

# Label patterns that identify an IR / international registration number
_IR_LABEL = re.compile(
    r'(?:IR\s+No\.?|International\s+Reg(?:istration)?\.?\s+(?:No\.?|Number)|'
    r'Reg\.?\s+int\.?)',
    re.IGNORECASE
)

# A digit string that could be a CIPO application number (7–8 digits, commas/spaces allowed)
_DIGIT_SEQ = re.compile(r'\b(\d[\d,\s]{4,9}\d)\b')


def _clean_num(s: str) -> Optional[str]:
    """Strip commas/spaces from a digit string and return if 7–8 digits."""
    n = re.sub(r'[\s,]', '', s)
    if re.fullmatch(r'\d{7,8}', n):
        return n
    return None


def _first_number(text: str) -> Optional[str]:
    """Return the first 7-or-8-digit number in a text string."""
    for m in _DIGIT_SEQ.finditer(text):
        n = _clean_num(m.group(1))
        if n:
            return n
    return None


def _extract_application_number(paragraphs: list[dict], tables=None) -> Optional[str]:
    """
    Find the CIPO application number, preferring explicitly labeled occurrences
    and avoiding IR (international registration) numbers.

    Strategy:
      1. Scan table rows: if one cell matches the APP_LABEL pattern, grab the
         number from that same cell or the next cell in the same row.
      2. Scan paragraphs for "Application No.*<digits>" inline patterns.
      3. Fall back to any 7-digit number in paragraphs that isn't IR-adjacent.
      4. Fall back to any 7-digit number in table cells that isn't IR-adjacent.
    """

    # ── Pass 1: table cells with explicit label ───────────────────────────────
    if tables:
        for table in tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                for i, cell_text in enumerate(cells):
                    if _APP_LABEL.search(cell_text):
                        # Number might be in the same cell after the label
                        after = _APP_LABEL.sub('', cell_text, count=1).strip(' :')
                        n = _first_number(after)
                        if n:
                            return n
                        # Or in the next cell
                        if i + 1 < len(cells):
                            n = _first_number(cells[i + 1])
                            if n:
                                return n

    # ── Pass 2: paragraphs with inline label ──────────────────────────────────
    for p in paragraphs:
        text = p["text"]
        if _APP_LABEL.search(text):
            after = _APP_LABEL.sub('', text, count=1).strip(' :')
            n = _first_number(after)
            if n:
                return n

    # ── Pass 3: any 7-digit number in paragraphs, skipping IR-adjacent ────────
    for p in paragraphs:
        text = p["text"]
        for m in _DIGIT_SEQ.finditer(text):
            n = _clean_num(m.group(1))
            if not n:
                continue
            before = text[max(0, m.start() - 40): m.start()]
            if _IR_LABEL.search(before):
                continue  # skip IR numbers
            return n

    # ── Pass 4: any 7-digit number in table cells, skipping IR-adjacent ───────
    if tables:
        for table in tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                for i, cell_text in enumerate(cells):
                    # Skip cells whose label cell is an IR label
                    label = cells[i - 1] if i > 0 else ""
                    if _IR_LABEL.search(label) or _IR_LABEL.search(cell_text):
                        continue
                    n = _first_number(cell_text)
                    if n:
                        return n

    return None


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
    """
    Return raw text from all paragraphs and table cells for debugging.
    Used by /api/debug-parse to diagnose extraction failures.
    """
    doc = Document(file_path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for t_idx, table in enumerate(doc.tables):
        rows = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        tables.append({"table_index": t_idx, "rows": rows})
    return {"paragraphs": paras[:40], "tables": tables}


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

    return ParsedOfficeAction(
        application_number=_extract_application_number(paragraphs_with_formatting, doc.tables),
        trademark_name=re_fields["trademark_name"],
        applicant_name=re_fields["applicant_name"],
        formatting_convention=None,
        full_text="\n".join(full_text_lines),
        paragraphs_with_formatting=paragraphs_with_formatting,
    )
