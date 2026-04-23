from __future__ import annotations

import anthropic
import httpx
import json
import os
import re


# ── Web research helpers ───────────────────────────────────────────────────────

def research_context(applicant_name: str, trademark_name: str) -> dict:
    """
    Use Claude's built-in web search to research the applicant's business and
    find evidence of trademark use online.
    Returns {applicant_blurb, applicant_url, trademark_blurb, trademark_url}.
    """
    if not applicant_name and not trademark_name:
        return {"applicant_blurb": None, "applicant_url": None, "trademark_blurb": None, "trademark_url": None}

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are assisting a Canadian trademark agent preparing an office action response.

Search the web for "{applicant_name}" and the trademark "{trademark_name}". Your goal is to understand:
1. What kind of business or entity "{applicant_name}" is — industry, what they make or sell, their website
2. Whether and how the trademark "{trademark_name}" is in active commercial use online, and what SPECIFIC goods or services are sold under it

Return ONLY a JSON object — no markdown, no explanation, no other text:
{{
  "applicant_blurb": "2–3 sentences describing what {applicant_name} does, their industry, and key products or services",
  "applicant_url": "the main URL for the applicant's website (e.g. homepage), or null if not found",
  "trademark_blurb": "2–3 sentences describing specifically how the trademark '{trademark_name}' is being used online — what products or services it appears on, what the website sells under that mark",
  "trademark_url": "the most direct URL showing the trademark in use (homepage, product page, etc.), or null"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    full_text = "".join(
        block.text for block in response.content
        if hasattr(block, "text") and block.text
    ).strip()

    if not full_text:
        return {"applicant_blurb": None, "applicant_url": None, "trademark_blurb": None, "trademark_url": None}

    if full_text.startswith("```"):
        full_text = full_text.split("```")[1]
        if full_text.startswith("json"):
            full_text = full_text[4:]

    return json.loads(full_text.strip())


# ── Amendment suggestions ──────────────────────────────────────────────────────

def generate_amendment_suggestions(
    term: str,
    nice_class: str,
    reason: str,
    gsm_matches: list,
    specificity_guidance: list,
    business_context: str = "",
) -> list[dict]:
    """
    Review ALL pre-approved G&S Manual matches and select the most applicable
    ones for this applicant, using business context for intelligent ranking.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Group GSM matches: same-class first, then cross-class
    same_class = [m for m in gsm_matches if m.get("nice_class","").lstrip("0") == str(int(nice_class or "0"))]
    cross_class = [m for m in gsm_matches if m not in same_class]

    def _fmt_gsm(matches):
        lines = []
        for m in matches:
            line = f'  • {m["term"]}'
            if m.get("nice_class","").lstrip("0") != str(int(nice_class or "0")):
                line += f' [Class {m["nice_class"].lstrip("0")}]'
            if m.get("notes"):
                line += f'  — {m["notes"]}'
            lines.append(line)
        return "\n".join(lines)

    gsm_section = ""
    if same_class:
        gsm_section += f"\nClass {nice_class} pre-approved terms containing \"{term}\" ({len(same_class)} total):\n{_fmt_gsm(same_class)}\n"
    if cross_class:
        gsm_section += f"\nPre-approved terms from other classes that may inform wording ({len(cross_class)} total):\n{_fmt_gsm(cross_class[:100])}\n"

    sg_block = ""
    if specificity_guidance:
        lines = "\n".join(
            f'  • "{g["term"]}": {g["guidance"]}'
            for g in specificity_guidance[:6]
        )
        sg_block = f"\nCIPO Specificity Guidelines:\n{lines}\n"

    context_block = f"\nApplicant context: {business_context}\n" if business_context else ""

    tier_note = (
        "Tier 1 = best match for this applicant's specific use case. Tier 2 = other valid options."
        if business_context
        else "Tier 1 = most specific and likely to satisfy the examiner. Tier 2 = alternatives."
    )

    prompt = f"""You are a Canadian trademark agent advising on a CIPO office action response.

Objected term: "{term}" (Class {nice_class})
Examiner's reason: {reason or "not specific enough / not in ordinary commercial terms"}
{context_block}{gsm_section}{sg_block}
Your task: review the complete list of pre-approved terms above and SELECT the 6–10 most applicable replacements for this specific applicant.

Rules:
1. STRONGLY prefer terms from the pre-approved list above — these will be accepted by CIPO without further objection
2. If recommending a term from the list, copy it EXACTLY as written
3. Only construct a new term (not from the list) if no pre-approved option adequately fits — label it "Constructed per specificity guidelines"
4. Each replacement must be more specific than "{term}"
5. {tier_note}

Return a JSON array only — no markdown, no explanation:
[
  {{
    "replacement": "exact term from the list, or constructed term",
    "rationale": "one sentence: why this fits this applicant's use case",
    "source": "CIPO G&S Manual, Class {nice_class}" or "CIPO G&S Manual, Class XX" or "Constructed per specificity guidelines",
    "tier": 1
  }}
]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Office action analysis ─────────────────────────────────────────────────────

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
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
