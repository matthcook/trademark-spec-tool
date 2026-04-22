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


def _extract_re_table(doc) -> dict:
    """Extract trademark name and applicant from the RE: header table."""
    result = {"trademark_name": None, "applicant_name": None}
    for table in doc.tables:
        cells = [cell.text.strip() for row in table.rows for cell in row.cells if cell.text.strip()]
        # Look for a table containing RE:/Trademark:/Applicant: labels
        if any(c in ("RE:", "Re:", "Trademark:", "Applicant:") for c in cells):
            for i, cell in enumerate(cells):
                if cell in ("Trademark:", "Marque de commerce:") and i + 1 < len(cells):
                    result["trademark_name"] = cells[i + 1].replace("\n", " ").strip()
                if cell in ("Applicant:", "Demandeur:") and i + 1 < len(cells):
                    result["applicant_name"] = cells[i + 1].replace("\n", " ").strip()
    return result


def _extract_application_number(paragraphs: list[dict]) -> Optional[str]:
    for p in paragraphs:
        matches = re.findall(r'\b(\d{7})\b', p["text"])
        if matches:
            return matches[0]
    return None


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
        application_number=_extract_application_number(paragraphs_with_formatting),
        trademark_name=re_fields["trademark_name"],
        applicant_name=re_fields["applicant_name"],
        formatting_convention=None,
        full_text="\n".join(full_text_lines),
        paragraphs_with_formatting=paragraphs_with_formatting,
    )
