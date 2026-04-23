import httpx
import json
import os
import re
import anthropic
from dataclasses import dataclass
from typing import Optional


@dataclass
class CIPOApplication:
    application_number: str
    trademark_name: Optional[str]
    applicant: Optional[str]
    status: Optional[str]
    filing_date: Optional[str]
    specification: Optional[str]
    source_url: str


def fetch_application(application_number: str) -> CIPOApplication:
    """
    Fetch trademark application details from CIPO via direct HTTP.
    Note: CIPO's old URL infrastructure redirects to a dead IP, so this
    currently returns a stub with no specification for most applications.
    The analysis still works; the UI shows an amber banner when spec is missing.
    """
    app_number_clean = re.sub(r'[^\d]', '', application_number.strip())
    url = f"https://www.ic.gc.ca/app/opic-cipo/trdmrks/srch/tm-md-dtls.do?lang=eng&tmId={app_number_clean}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
    }

    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            response = client.get(url, headers=headers)
            if response.status_code == 200 and len(response.text) > 500:
                return _parse_cipo_html(app_number_clean, response.text, url)
    except Exception:
        pass

    return CIPOApplication(
        application_number=app_number_clean,
        trademark_name=None,
        applicant=None,
        status=None,
        filing_date=None,
        specification=None,
        source_url=url,
    )


def _fetch_via_web_search(app_number: str, fallback_url: str) -> Optional[CIPOApplication]:
    """
    Use Claude's web search to look up the CIPO trademark application and
    extract the goods/services specification.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Look up Canadian trademark application number {app_number} on the CIPO (Canadian Intellectual Property Office) trademark database.

Search for it directly on the CIPO website. Extract the complete goods and services specification — this is the full list of goods and/or services the trademark covers, organized by Nice class.

Return ONLY a JSON object — no markdown, no explanation:
{{
  "trademark_name": "the trademark name, or null",
  "applicant": "the applicant/owner name, or null",
  "status": "application status, or null",
  "filing_date": "filing date, or null",
  "specification": "the COMPLETE goods and services specification text, exactly as shown on CIPO — include all classes and all terms. If multiple classes, include all of them. Return null if not found.",
  "source_url": "the URL where you found this information, or null"
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        full_text = "".join(
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        ).strip()

        if not full_text:
            return None

        if full_text.startswith("```"):
            full_text = full_text.split("```")[1]
            if full_text.startswith("json"):
                full_text = full_text[4:]

        data = json.loads(full_text.strip())

        return CIPOApplication(
            application_number=app_number,
            trademark_name=data.get("trademark_name"),
            applicant=data.get("applicant"),
            status=data.get("status"),
            filing_date=data.get("filing_date"),
            specification=data.get("specification"),
            source_url=data.get("source_url") or fallback_url,
        )
    except Exception:
        return None


def _parse_cipo_html(app_number: str, html: str, source_url: str) -> CIPOApplication:
    def extract(pattern: str) -> Optional[str]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else None

    def clean(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip() or None

    return CIPOApplication(
        application_number=app_number,
        trademark_name=clean(extract(r'<th[^>]*>\s*Trademark\s*</th>\s*<td[^>]*>(.*?)</td>')),
        applicant=clean(extract(r'<th[^>]*>\s*Applicant\s*</th>\s*<td[^>]*>(.*?)</td>')),
        status=clean(extract(r'<th[^>]*>\s*Status\s*</th>\s*<td[^>]*>(.*?)</td>')),
        filing_date=clean(extract(r'<th[^>]*>\s*Filing date\s*</th>\s*<td[^>]*>(.*?)</td>')),
        specification=clean(extract(r'<th[^>]*>\s*(?:Goods and Services|Statement of goods)\s*</th>\s*<td[^>]*>(.*?)</td>')),
        source_url=source_url,
    )
