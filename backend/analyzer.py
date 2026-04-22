import anthropic
import json
import os


def analyze_office_action(parsed_doc, cipo_app=None) -> dict:
    """
    Use Claude to interpret the office action:
    - Identify the examiner's formatting convention
    - Extract each objected term with its reason and Nice class
    - Return structured JSON
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build the tagged document text for Claude
    tagged_paragraphs = "\n".join(
        p["tagged"] for p in parsed_doc.paragraphs_with_formatting
    )

    spec_context = ""
    if cipo_app and cipo_app.specification:
        spec_context = f"""
The current specification on file at CIPO is:
{cipo_app.specification}
"""

    prompt = f"""You are analyzing a Canadian CIPO trademark examiner's office action (also called an examiner's report).

The document uses special tags to indicate text formatting:
- [UNDERLINE]...[/UNDERLINE] = underlined text
- [BOLD]...[/BOLD] = bold text
- [BOLD_UNDERLINE]...[/BOLD_UNDERLINE] = bold and underlined text

Here is the full office action with formatting tags:

{tagged_paragraphs}

{spec_context}

Your task is to extract structured information from this office action. Return a JSON object with exactly this structure:

{{
  "application_number": "the 7-digit application number",
  "response_deadline": "the deadline date for responding, as written in the document",
  "examiner_name": "the examiner's name",
  "formatting_convention": "one sentence describing what formatting means what — e.g. 'Underlined terms are not specific or not in ordinary commercial terms per s.29 of the Trademarks Regulations'",
  "objections": [
    {{
      "nice_class": "class number as a string, e.g. '09'",
      "goods_or_services": "Goods or Services",
      "objected_term": "the exact term that was objected to",
      "objection_reason": "brief reason — e.g. 'not in ordinary commercial terms', 'wrong Nice class', 'grammatical error'",
      "full_context": "the full sentence or phrase containing the objected term"
    }}
  ]
}}

Important:
- Each unique objected term should appear once per context it appears in (e.g. if 'equipment' is underlined 4 times across 4 different goods, list it 4 times with different full_context values)
- Only include terms that are actually marked with formatting indicating an objection
- Return only valid JSON with no additional text
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)
