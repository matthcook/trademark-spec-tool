import anthropic
import json
import os


def generate_amendment_suggestions(
    term: str,
    nice_class: str,
    reason: str,
    gsm_matches: list,
    specificity_guidance: list,
) -> list[dict]:
    """Ask Claude to propose specific replacement terms with citations."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    gsm_block = ""
    if gsm_matches:
        lines = "\n".join(
            f'  - "{m["term"]}"' + (f' (note: {m["notes"]})' if m.get("notes") else "")
            for m in gsm_matches[:15]
        )
        gsm_block = f"\nPre-approved G&S Manual terms for Class {nice_class} that partially match:\n{lines}\n"

    sg_block = ""
    if specificity_guidance:
        lines = "\n".join(
            f'  - "{g["term"]}": {g["guidance"]}'
            for g in specificity_guidance[:8]
        )
        sg_block = f"\nCIPO Specificity Guidelines relevant entries:\n{lines}\n"

    prompt = f"""You are a Canadian trademark agent advising on a CIPO office action response.

The examiner has objected to the following term in Class {nice_class}:
  Term: "{term}"
  Examiner's reason: {reason or "not specific enough / not in ordinary commercial terms"}
{gsm_block}{sg_block}
Your task: propose 3–5 specific replacement terms that would satisfy the examiner.

Rules:
- Prefer terms already in the CIPO pre-approved G&S Manual (listed above) — cite them as source
- If constructing a new term, follow specificity guidelines and Canadian trademark practice
- Each replacement must be more specific than the objected term
- Keep replacements concise (as they appear in a trademark specification)
- Do not include terms that span multiple classes

Return a JSON array with this exact structure (no markdown fences, no extra text):
[
  {{
    "replacement": "the proposed replacement term",
    "rationale": "one sentence explaining why this satisfies the examiner",
    "source": "CIPO G&S Manual, Class {nice_class}" or "Constructed per specificity guidelines"
  }}
]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


def analyze_office_action(parsed_doc, cipo_app=None) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    tagged_paragraphs = "\n".join(
        p["tagged"] for p in parsed_doc.paragraphs_with_formatting
    )

    prompt = f"""You are analyzing a Canadian CIPO trademark examiner's office action.

The document uses special tags to indicate text formatting:
- [UNDERLINE]...[/UNDERLINE] = underlined text
- [BOLD]...[/BOLD] = bold text
- [BOLD_UNDERLINE]...[/BOLD_UNDERLINE] = bold and underlined text

Here is the full office action with formatting tags:

{tagged_paragraphs}

Your task is to extract structured information and return a JSON object with exactly this structure:

{{
  "application_number": "the 7-digit application number",
  "trademark_name": "the trademark name if present in the document, otherwise null",
  "applicant_name": "the applicant name if present in the document, otherwise null",
  "response_deadline": "the deadline date for responding, as written in the document",
  "examiner_name": "the examiner's name",
  "formatting_convention": "one sentence describing what the examiner's formatting indicates — e.g. 'Underlined terms are not specific or not in ordinary commercial terms per s.29 of the Trademarks Regulations and s.30(2)(a) of the Trademarks Act'",
  "classes": [
    {{
      "nice_class": "class number as a string, e.g. '09'",
      "goods_or_services": "Goods or Services",
      "marked_text": "the COMPLETE text of this class entry, with every objected term wrapped in {{{{ }}}} markers. For example: 'Software for monitoring vehicles, {{{{equipment}}}}, and {{{{heavy machinery}}}}'",
      "objected_terms": [
        {{
          "term": "the exact objected term as it appears in the text",
          "reason": "brief reason e.g. 'not in ordinary commercial terms'"
        }}
      ]
    }}
  ]
}}

Critical rules:
- In marked_text, wrap EVERY occurrence of EVERY objected term in {{{{ }}}} markers — do not skip any occurrence
- Include ALL classes from the specification, even those with no objected terms (objected_terms will be an empty array)
- Reconstruct the complete specification text for each class — do not truncate
- Remove the [BOLD][09][/BOLD] class number prefix from marked_text — start with the goods/services text itself
- Return only valid JSON with no additional text
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)
