import httpx
import re
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
    Attempt to fetch trademark application details from CIPO.
    Returns a CIPOApplication with whatever fields could be retrieved.
    Full application lookup via the CIPO bulk database will be available in Phase 3.
    """
    app_number_clean = re.sub(r'[^\d]', '', application_number.strip())

    # Try the CIPO trademark detail page
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

    # Return a stub — Phase 3 will populate this from the local bulk database
    return CIPOApplication(
        application_number=app_number_clean,
        trademark_name=None,
        applicant=None,
        status=None,
        filing_date=None,
        specification=None,
        source_url=url,
    )


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
