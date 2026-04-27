from __future__ import annotations

import anthropic
import httpx
import json
import os
import re
import time


def _claude_create_with_retry(client, max_retries: int = 4, **kwargs):
    """Call client.messages.create with exponential back-off on 529 overload errors."""
    delay = 5
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            if exc.status_code in (429, 529) and attempt < max_retries - 1:
                # 429 = rate limit (wait longer); 529 = overloaded (shorter wait)
                wait = delay * 2 if exc.status_code == 429 else delay
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            raise


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

Search the web for the applicant "{applicant_name}" and their trademark "{trademark_name}".

Task 1 — Applicant research:
Search for "{applicant_name}" to find their website and understand their business. Look for their homepage, about page, LinkedIn, or other authoritative sources.

Task 2 — Trademark use by THIS applicant only:
Search specifically for use of "{trademark_name}" BY "{applicant_name}" — for example by searching for both names together. You are looking for evidence that {applicant_name} uses "{trademark_name}" as a brand name on products or services. Do NOT report use of "{trademark_name}" by unrelated third parties. Do NOT guess or construct a URL like "{trademark_name.lower().replace(' ', '')}.com" — only return a URL you actually found in search results that shows {applicant_name} using "{trademark_name}".

If you cannot find any evidence that {applicant_name} uses "{trademark_name}" online, say so explicitly in trademark_blurb and set trademark_url to null.

Return ONLY a JSON object — no markdown, no explanation, no other text:
{{
  "applicant_blurb": "2–3 sentences describing what {applicant_name} does, their industry, and key products or services",
  "applicant_url": "URL of the applicant's own website found in search results, or null if not found",
  "trademark_blurb": "2–3 sentences describing how {applicant_name} uses '{trademark_name}' online, with specific goods/services — OR a clear statement that no online use by {applicant_name} was found",
  "trademark_url": "a URL you actually found showing {applicant_name} using '{trademark_name}', or null"
}}"""

    _empty = {"applicant_blurb": None, "applicant_url": None, "trademark_blurb": None, "trademark_url": None}

    def _call(use_search: bool) -> dict:
        tools = [{"type": "web_search_20250305", "name": "web_search"}] if use_search else []
        resp = _claude_create_with_retry(
            client,
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

_CONJUNCTION_FIXES: dict[str, list[dict]] = {
    "or": [{"replacement": "and", "rationale": "CIPO requires inclusive listing — 'or' creates ambiguity about what is actually claimed; replace with 'and'", "source": "CIPO Practice", "tier": 1}],
    "and/or": [{"replacement": "and", "rationale": "CIPO does not accept 'and/or'; use 'and' to list all goods/services inclusively", "source": "CIPO Practice", "tier": 1}],
    "etc": [{"replacement": "[remove — list all items explicitly]", "rationale": "CIPO requires a complete and definite list; 'etc.' is not acceptable", "source": "CIPO Practice", "tier": 1}],
    "etc.": [{"replacement": "[remove — list all items explicitly]", "rationale": "CIPO requires a complete and definite list; 'etc.' is not acceptable", "source": "CIPO Practice", "tier": 1}],
    "e.g.": [{"replacement": "namely,", "rationale": "Replace informal 'e.g.' with the trade-mark specification convention 'namely,'", "source": "CIPO Practice", "tier": 1}],
    "i.e.": [{"replacement": "namely,", "rationale": "Replace informal 'i.e.' with the trade-mark specification convention 'namely,'", "source": "CIPO Practice", "tier": 1}],
}


def generate_amendment_suggestions(
    term: str,
    nice_class: str,
    reason: str,
    gsm_matches: list,
    specificity_guidance: list,
    business_context: str = "",
    term_context: str = "",
) -> list[dict]:
    """
    Review ALL pre-approved G&S Manual matches and select the most applicable
    ones for this applicant, using business context for intelligent ranking.
    """
    # Fast-path: conjunctions and grammar words need a direct fix, not GSM suggestions
    fix_key = term.strip().lower()
    if fix_key in _CONJUNCTION_FIXES:
        return _CONJUNCTION_FIXES[fix_key]

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
    context_block += f'\nFull good/service containing the objected term: "{term_context}"\n' if term_context else ""

    tier_note = (
        "Tier 1 = best match for this applicant's specific use case. Tier 2 = other valid options."
        if business_context
        else "Tier 1 = most specific and likely to satisfy the examiner. Tier 2 = alternatives."
    )

    words = term.strip().split()
    is_single_word = len(words) == 1
    is_complex = len(words) > 3

    if is_single_word and term_context:
        # The examiner objected to one word within a larger good/service.
        # Show the full segment so Claude understands what's actually being fixed.
        specificity_rule = (
            f'"{term}" is a single word within the good/service "{term_context}". '
            f'Suggestions must replace "{term}" with a more specific word or short phrase that fits '
            f'naturally into the surrounding context — do NOT suggest a completely different good/service. '
            f'Each suggestion should result in a grammatically correct, CIPO-accepted version of the full term.'
        )
    elif is_complex:
        specificity_rule = (
            f'"{term}" is a multi-word phrase. Replacements must describe the same category of goods/services '
            f'but replace the vague or overbroad portions with specific, concrete language. '
            f'The replacement does not need to use the same words — it should describe the specific type of '
            f'product/service in plain commercial terms.'
        )
    else:
        specificity_rule = (
            f'Each replacement must be more specific than "{term}" and accurately describe '
            f'the goods/services in plain commercial terms.'
        )

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

    message = _claude_create_with_retry(
        client,
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


# ── Grammar & duplicate checker ───────────────────────────────────────────────

def check_grammar(classes: list[dict]) -> list[dict]:
    """
    Review all spec classes for grammar errors and duplicate goods/services.
    Returns a list of {nice_class, excerpt, issue_type, severity, description, suggestion}.
    """
    if not classes:
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    spec_lines = []
    for cls in classes:
        # Prefer current_text (live edited state) over marked_text (original)
        raw = cls.get("current_text") or cls.get("marked_text", "") or ""
        text = re.sub(r'\{\{([^}]+)\}\}', r'\1', raw).strip()
        if text:
            spec_lines.append(f"Class {cls.get('nice_class','?')}: {text}")

    if not spec_lines:
        return []

    prompt = f"""You are a Canadian trademark agent carefully proofreading a trademark specification before filing a response to a CIPO office action. Any error you miss could trigger another office action.

SPECIFICATION TO REVIEW:
{chr(10).join(spec_lines)}

Terms within each class are separated by semicolons. Each semicolon-delimited segment is one complete good or service.

YOUR TASK — identify only genuine errors in these four categories:

1. duplicate_goods — Two COMPLETE goods/services (entire semicolon-delimited segments) are substantively identical, meaning:
   (a) verbatim or near-verbatim duplicates (e.g. the segment "computer software" appears twice), OR
   (b) the same items listed in a different order (e.g. "clothing, namely, shirts, pants, and shoes" and "clothing, namely, pants, shoes, and shirts"), OR
   (c) one segment uses synonyms or paraphrasing of another such that a trademark examiner would consider them the same good/service. This is the most important category. Examples of synonym-based duplicates that MUST be flagged:
      • "prepared dishes consisting principally of meat" and "prepared meals consisting primarily of meat" — DUPLICATE ("dishes" ≈ "meals", "principally" ≈ "primarily")
      • "downloadable software for managing financial records" and "downloadable software for the management of financial records" — DUPLICATE
      • "retail sale of clothing" and "retail services featuring clothing" — DUPLICATE
   CRITICAL — Do NOT flag:
   • A word or phrase that simply appears in many different terms. "Computer software" appearing in 10 different terms is completely normal.
   • A SHORT standalone term that also appears as words within a LONGER term. For example, "clothing" as one segment and "clothing, namely, shirts and pants" as another segment are NOT duplicates — they have different scope. Only flag when two segments cover the exact same scope.
   Only flag COMPLETE SEGMENT duplicates where both segments describe the same goods/services.
   Do NOT suggest consolidating — that is not a practical solution. Instead describe which segments are duplicates.

2. grammar — A phrase is structurally broken: missing a key word, wrong word order, truncated sentence, or a preposition left dangling with nothing following it (e.g. "software for the", "retail of").
   Do NOT flag terms that are simply unconventional but clear.

3. missing_punctuation — A comma is clearly required inside a term for grammatical correctness, OR two separate goods/services have been run together without a semicolon between them.
   NOTE: Semicolons between separate goods/services are CORRECT and must NOT be flagged.

4. duplicate_word — The same word appears twice consecutively within a single term (e.g. "computer computer software"), which is clearly a typo.

Return ONLY a JSON array — empty array [] if there are no issues:
[
  {{
    "nice_class": "09",
    "excerpt": "the complete duplicate segment or the exact broken phrase, ≤60 chars",
    "issue_type": "duplicate_goods" | "grammar" | "missing_punctuation" | "duplicate_word",
    "severity": "error",
    "description": "one precise sentence — for duplicates, name both duplicate segments",
    "suggestion": "specific corrected text or action — do NOT suggest consolidating"
  }}
]"""

    message = _claude_create_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=2000,
        temperature=0,
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

    if not raw.startswith("["):
        m = re.search(r'\[[\s\S]*\]', raw)
        raw = m.group(0) if m else "[]"

    return json.loads(raw)


# ── Personal spec library parsing ────────────────────────────────────────────

def parse_spec_into_terms(text: str) -> list[dict]:
    """
    Use Claude to parse a trademark specification (plain text or extracted from
    a Word doc) into a flat list of {nice_class, term} dicts.
    Each term is one individual good or service as it would appear in the spec.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""Parse this trademark specification into individual goods and services.

The specification text:
{text[:6000]}

Return a JSON array of objects — no markdown, no explanation:
[
  {{"nice_class": "09", "term": "downloadable computer software for fleet tracking"}},
  {{"nice_class": "35", "term": "retail store services featuring computer hardware"}}
]

Rules:
- Split the specification by Nice class (look for "Class XX:" headers or similar)
- Within each class, split on semicolons to get individual terms
- Clean up each term: trim whitespace, remove trailing punctuation
- Preserve the full term text exactly as written — do not summarize or shorten
- nice_class should be a zero-padded two-digit string, e.g. "09" not "9"
- Return every term, including those that appear acceptable (not objected)
- If the text has multiple applications, parse all of them"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
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

    if not raw.startswith("["):
        m = re.search(r'\[[\s\S]*\]', raw)
        raw = m.group(0) if m else "[]"

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

    message = _claude_create_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
