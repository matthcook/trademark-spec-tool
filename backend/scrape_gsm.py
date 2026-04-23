"""
Scrapes the CIPO Goods and Services Manual via direct HTTP requests.

The browser app calls /browse/alpha.json to get a fresh API token,
then calls the terms API with that token. We replicate the same flow.

Run with:
    ../venv/bin/python scrape_gsm.py
"""

import json
import sqlite3
import os
import httpx
from datetime import datetime

GSM_URL    = "https://www.ic.gc.ca/app/scr/ic/cgs/ext/search.html"
CREDS_URL  = "https://www.ic.gc.ca/app/scr/ic/cgs/ext/browse/alpha.json"
DB_PATH    = os.path.join(os.path.dirname(__file__), "data", "cipo.db")
LETTERS    = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": GSM_URL,
}


def init_gsm_table():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gsm_terms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nice_class  TEXT NOT NULL,
            term        TEXT NOT NULL,
            term_status INTEGER,
            notes       TEXT,
            source      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gsm_class ON gsm_terms(nice_class);
        CREATE INDEX IF NOT EXISTS idx_gsm_term  ON gsm_terms(term COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS resource_metadata (
            resource    TEXT PRIMARY KEY,
            downloaded  TEXT,
            source_url  TEXT
        );
    """)
    # Add columns that may be missing from an older schema
    existing = {row[1] for row in conn.execute("PRAGMA table_info(gsm_terms)")}
    for col, typedef in [("term_status", "INTEGER"), ("notes", "TEXT"), ("source", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE gsm_terms ADD COLUMN {col} {typedef}")
    conn.commit()
    conn.close()


def save_terms(records: list[dict]):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM gsm_terms")
    conn.executemany(
        """INSERT INTO gsm_terms (nice_class, term, term_status, notes, source)
           VALUES (:nice_class, :term, :term_status, :notes, :source)""",
        records
    )
    conn.execute(
        "INSERT OR REPLACE INTO resource_metadata VALUES ('gsm', ?, ?)",
        (datetime.utcnow().isoformat(), GSM_URL)
    )
    conn.commit()
    conn.close()


def get_fresh_credentials(client: httpx.Client) -> tuple[str, str, str]:
    """Fetch a fresh API token from the alpha.json endpoint."""
    r = client.get(CREDS_URL, headers=BASE_HEADERS, params={"adminPage": "false"})
    r.raise_for_status()
    data = r.json()
    return data["apiUrl"], data["apiAuthorization"], data["apiVersion"]


def fetch_terms_for_letter(
    client: httpx.Client,
    api_url: str,
    api_auth: str,
    api_version: str,
    letter: str,
) -> list[dict]:
    """Fetch all pre-approved terms beginning with a given letter."""
    headers = dict(BASE_HEADERS)
    headers["Authorization"] = api_auth
    headers["api-version"]   = api_version

    r = client.get(api_url, headers=headers, params={
        "lang":       "en",
        "searchType": "BEGINS",
        "termNames":  letter,
    })
    r.raise_for_status()
    data = r.json()

    records = []
    for item in data.get("result", [{}])[0].get("resultsReturned", []):
        term_name = (item.get("termName") or "").strip()
        if not term_name:
            continue

        # Collect Nice classes
        nice_classes = []
        for cls in item.get("niceClasses") or []:
            num = cls.get("number") or ""
            if num:
                nice_classes.append(str(num).lstrip("0").zfill(2))

        if not nice_classes:
            nice_classes = ["??"]

        notes       = (item.get("notesEn") or "").strip() or None
        term_status = item.get("termStatus", 1)

        for cls in nice_classes:
            records.append({
                "nice_class":  cls,
                "term":        term_name,
                "term_status": term_status,
                "notes":       notes,
                "source":      "gsm",
            })

    return records


def scrape():
    all_records = []
    seen_terms  = set()

    print("Fetching CIPO G&S Manual terms via direct API calls...")

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        # Get credentials once — they're valid for the full session
        print("Getting API credentials...")
        api_url, api_auth, api_version = get_fresh_credentials(client)
        print(f"  API endpoint: {api_url}")

        for letter in LETTERS:
            print(f"  Letter {letter}...", end=" ", flush=True)
            try:
                records = fetch_terms_for_letter(client, api_url, api_auth, api_version, letter)
                new = 0
                for r in records:
                    key = (r["term"].lower(), r["nice_class"])
                    if key not in seen_terms:
                        seen_terms.add(key)
                        all_records.append(r)
                        new += 1
                print(f"{new} terms")
            except Exception as e:
                # Token may have expired; try refreshing once
                print(f"retrying...", end=" ", flush=True)
                try:
                    api_url, api_auth, api_version = get_fresh_credentials(client)
                    records = fetch_terms_for_letter(client, api_url, api_auth, api_version, letter)
                    new = 0
                    for r in records:
                        key = (r["term"].lower(), r["nice_class"])
                        if key not in seen_terms:
                            seen_terms.add(key)
                            all_records.append(r)
                            new += 1
                    print(f"{new} terms")
                except Exception as e2:
                    print(f"skipped ({e2})")

    print(f"\nTotal unique term-class pairs collected: {len(all_records)}")

    if len(all_records) < 10:
        print("ERROR: Very few terms found. The API may have changed.")
        return

    init_gsm_table()
    save_terms(all_records)
    print("Saved to database.")


if __name__ == "__main__":
    scrape()
