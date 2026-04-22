from docx import Document
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FormattedRun:
    text: str
    bold: bool
    underline: bool


@dataclass
class ObjectedItem:
    term: str
    context: str
    nice_class: Optional[str]
    goods_or_services: str


@dataclass
class ParsedOfficeAction:
    application_number: Optional[str]
    formatting_convention: Optional[str]
    full_text: str
    paragraphs_with_formatting: list[dict]
    objected_items: list[ObjectedItem] = field(default_factory=list)


def extract_application_number(paragraphs: list[dict]) -> Optional[str]:
    """Find a 7-digit CIPO application number in the document."""
    import re
    for p in paragraphs:
        matches = re.findall(r'\b(\d{7})\b', p["text"])
        if matches:
            return matches[0]
    return None


def parse_office_action(file_path: str) -> ParsedOfficeAction:
    doc = Document(file_path)
    paragraphs_with_formatting = []
    full_text_lines = []

    current_nice_class = None

    for para in doc.paragraphs:
        if not para.text.strip():
            continue

        runs = []
        for run in para.runs:
            runs.append(FormattedRun(
                text=run.text,
                bold=bool(run.bold),
                underline=bool(run.underline),
            ))

        # Build a tagged version of the paragraph for the AI to read
        tagged = ""
        for run in runs:
            t = run.text
            if run.bold and run.underline:
                t = f"[BOLD_UNDERLINE]{t}[/BOLD_UNDERLINE]"
            elif run.bold:
                t = f"[BOLD]{t}[/BOLD]"
            elif run.underline:
                t = f"[UNDERLINE]{t}[/UNDERLINE]"
            tagged += t

        paragraphs_with_formatting.append({
            "text": para.text,
            "tagged": tagged,
            "runs": [{"text": r.text, "bold": r.bold, "underline": r.underline} for r in runs],
        })
        full_text_lines.append(para.text)

    full_text = "\n".join(full_text_lines)
    app_number = extract_application_number(paragraphs_with_formatting)

    return ParsedOfficeAction(
        application_number=app_number,
        formatting_convention=None,  # filled in by AI analyzer
        full_text=full_text,
        paragraphs_with_formatting=paragraphs_with_formatting,
    )
