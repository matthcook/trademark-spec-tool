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

    _empty = {"applicant_blurb": None, "applicant_url": None, "trademark_blurb": None, "trademark_url": None}

    def _call(use_search: bool) -> dict:
        tools = [{"type": "web_search_20250305", "name": "web_search"}] if use_search else []
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            tools=tools,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in resp.content if hasattr(b, "text") and b.text
        ).strip()
        if not text:
            return _empty
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        # If JSON is embedded in prose, extract the first {...} block
        if not text.startswith("{"):
            m = re.search(r'\{[\s\S]*"applicant_blurb"[\s\S]*\}', text)
            if m:
                text = m.group(0)
            else:
                return _empty
        return json.loads(text)

    try:
        result = _call(use_search=True)
    except Exception:
        result = _empty

    # If web search produced nothing, try once more without it (uses training data)
    if not result.get("applicant_blurb") and not result.get("trademark_blurb"):
        try:
            result = _call(use_search=False)
        except Exception:
            result = _empty

    return result


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

    # Adapt instructions for single words vs. complex multi-word phrases
    is_complex = len(term.strip().split()) > 3
    if is_complex:
        specificity_rule = (
            f'"{term}" is a multi-word phrase. Replacements must describe the same category of goods/services '
            f'but replace the vague or overbroad portions with specific, concrete language. '
            f'The replacement does not need to use the same words — it should describe the specific type of '
            f'product/service in plain commercial terms (e.g. "downloadable software for fleet vehicle tracking" '
            f'rather than "software for monitoring and analyzing vehicle data").'
        )
    else:
        specificity_rule = f'Each replacement must be more specific than "{term}".'

    prompt = f"""You are a Canadian trademark agent advising on a CIPO office action response.

Objected term: "{term}" (Class {nice_class})
Examiner's reason: {reason or "not specific enough / not in ordinary commercial terms"}
{context_block}{gsm_section}{sg_block}
Your task: scan the ENTIRE pre-approved list above and SELECT the 10–15 best replacements for this applicant.

Rules:
1. STRONGLY prefer terms from the pre-approved list — these are accepted by CIPO without further objection
2. Copy every recommended pre-approved term EXACTLY as written — do not paraphrase or shorten
3. Read the full list before deciding — do not stop at the first plausible matches
4. {specificity_rule}
5. Include a range of specificity levels: some narrowly tailored to the applicant's exact use, some broader pre-approved options that still satisfy the examiner
6. {tier_note}
7. Only construct a new term if no pre-approved option adequately fits — label source "Constructed per specificity guidelines"

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
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = ""
    for block in message.content:
        if hasattr(block, "text") and block.text:
            raw = block.text.strip()
            break

    if not raw:
        return []

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # If JSON array is embedded in prose, extract it
    if not raw.startswith("["):
        m = re.search(r'\[[\s\S]*\]', raw)
        if m:
            raw = m.group(0)
        else:
            return []

    return json.loads(raw)


# ── Office action analysis ─────────────────────────────────────────────────────

def analyze_office_action(parsed_doc, cipo_app=None) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    tagged_paragraphs = "\n".join(
        p["tagged"] for p in parsed_doc.paragraphs_with_formatting
    )

    # If we have the full CIPO spec, include it so Claude can fill gaps
    cipo_spec_block = ""
    if cipo_app and cipo_app.specification:
        cipo_spec_block = f"""
The complete goods and services specification currently on file at CIPO for this application is:

{cipo_app.specification}

IMPORTANT: Office actions sometimes only reproduce the objected classes or truncate the specification. Use the CIPO record above to reconstruct the COMPLETE specification text for every class, including classes that may not appear in the office action body. The marked_text for each class must reflect the full CIPO specification text, not just what the examiner quoted.
"""

    prompt = f"""You are analyzing a Canadian CIPO trademark examiner's office action.

The document uses special tags to indicate text formatting:
- [UNDERLINE]...[/UNDERLINE] = underlined text
- [BOLD]...[/BOLD] = bold text
- [BOLD_UNDERLINE]...[/BOLD_UNDERLINE] = bold and underlined text

Here is the full office action with formatting tags:

{tagged_paragraphs}
{cipo_spec_block}
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
- Include ALL classes from the full specification, even those with no objected terms (objected_terms will be an empty array)
- Use the complete CIPO specification text for each class — do not truncate or paraphrase
- Remove the [BOLD][09][/BOLD] class number prefix from marked_text — start with the goods/services text itself
- Objected terms may be single words OR multi-word phrases — wrap the entire objected phrase in {{{{ }}}}
- Return only valid JSON with no additional text
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
