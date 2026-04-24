"""
Downloads, parses, and indexes CIPO reference resources:
- Specificity Guidelines for the Goods and Services Manual (sggsm)
- Trademarks Examination Manual (TEM)

Stores everything in a local SQLite database at backend/data/cipo.db
"""

import os
import re
import sqlite3
import httpx
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH  = os.path.join(DATA_DIR, "cipo.db")

SGGSM_URL = "https://manuels-manuals-opic-cipo.s3.ca-central-1.amazonaws.com/sggsm-en.html"
TEM_URL   = "https://s3.ca-central-1.amazonaws.com/manuels-manuals-opic-cipo/TEM_En.html"


# ── Database setup ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specificity_guidelines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nice_class  TEXT NOT NULL,
            term        TEXT NOT NULL,
            guidance    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sg_class ON specificity_guidelines(nice_class);
        CREATE INDEX IF NOT EXISTS idx_sg_term  ON specificity_guidelines(term COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS tem_sections (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            heading  TEXT NOT NULL,
            content  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tem_heading ON tem_sections(heading COLLATE NOCASE);

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

        CREATE VIRTUAL TABLE IF NOT EXISTS gsm_fts USING fts5(
            term,
            nice_class UNINDEXED,
            notes      UNINDEXED,
            tokenize='unicode61 remove_diacritics 1'
        );
    """)
    # Add columns that may be missing from an older gsm_terms schema
    existing = {row[1] for row in conn.execute("PRAGMA table_info(gsm_terms)")}
    for col, typedef in [("term_status", "INTEGER"), ("notes", "TEXT"), ("source", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE gsm_terms ADD COLUMN {col} {typedef}")
    conn.commit()
    conn.close()


# ── Fetch helpers ──────────────────────────────────────────────────────────────

def _fetch(url: str) -> str:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content.decode("latin-1")


def _strip_tags(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()


# ── Specificity Guidelines parser ──────────────────────────────────────────────

def _parse_sggsm(html: str) -> list[dict]:
    """
    Parse the CIPO Specificity Guidelines HTML into a list of
    {nice_class, term, guidance} records.

    Structure in the document:
      "Class 1 term1 [guidance...] term2 [guidance...] Class 2 ..."
    """
    text = _strip_tags(html)

    # Find the start of the actual guidelines content
    start = text.find("Class 1 ")
    if start == -1:
        start = 0
    text = text[start:]

    records = []
    # Split on class headers: "Class 1 ", "Class 2 ", ..., "Class 45 ", "Unclassifiable "
    class_pattern = re.compile(
        r'(?:^|(?<=\s))(Class\s+(\d{1,2})|Unclassifiable)\s+'
    )
    class_splits = list(class_pattern.finditer(text))

    for i, match in enumerate(class_splits):
        nice_class = match.group(2) if match.group(2) else "Unclassifiable"
        nice_class = nice_class.zfill(2) if nice_class.isdigit() else nice_class

        # Text for this class: from end of this match to start of next
        end = class_splits[i + 1].start() if i + 1 < len(class_splits) else len(text)
        class_text = text[match.end():end].strip()

        # Each entry: "term [guidance]"
        entry_pattern = re.compile(r'([^\[]+?)\s*\[([^\]]+)\]')
        for entry_match in entry_pattern.finditer(class_text):
            term     = entry_match.group(1).strip().lower()
            guidance = entry_match.group(2).strip()
            if term and guidance and len(term) < 200:
                records.append({
                    "nice_class": nice_class,
                    "term":       term,
                    "guidance":   guidance,
                })

    return records


# ── TEM parser ─────────────────────────────────────────────────────────────────

def _parse_tem(html: str) -> list[dict]:
    """
    Extract headed sections from the TEM.
    Only keeps sections relevant to goods/services specification.
    """
    # Pull out h2/h3 headings and their body text
    section_pattern = re.compile(
        r'<h[23][^>]*>(.*?)</h[23]>(.*?)(?=<h[23]|$)',
        re.DOTALL | re.IGNORECASE
    )
    relevant_keywords = [
        "goods", "services", "specification", "specific", "ordinary commercial",
        "nice class", "classification", "section 29", "section 30",
        "statement of", "wares", "amendment"
    ]

    sections = []
    for m in section_pattern.finditer(html):
        heading = _strip_tags(m.group(1)).strip()
        content = _strip_tags(m.group(2)).strip()
        if (
            content
            and any(kw in heading.lower() or kw in content.lower()[:500]
                    for kw in relevant_keywords)
        ):
            sections.append({"heading": heading, "content": content[:4000]})

    return sections


# ── Load into DB ───────────────────────────────────────────────────────────────

def load_sggsm():
    print("Downloading CIPO Specificity Guidelines...")
    html    = _fetch(SGGSM_URL)
    records = _parse_sggsm(html)
    print(f"  Parsed {len(records)} entries.")

    conn = get_db()
    conn.execute("DELETE FROM specificity_guidelines")
    conn.executemany(
        "INSERT INTO specificity_guidelines (nice_class, term, guidance) VALUES (:nice_class, :term, :guidance)",
        records
    )
    conn.execute(
        "INSERT OR REPLACE INTO resource_metadata VALUES ('sggsm', ?, ?)",
        (datetime.utcnow().isoformat(), SGGSM_URL)
    )
    conn.commit()
    conn.close()
    print(f"  Loaded into database.")


def load_tem():
    print("Downloading CIPO Trademarks Examination Manual...")
    html     = _fetch(TEM_URL)
    sections = _parse_tem(html)
    print(f"  Parsed {len(sections)} relevant sections.")

    conn = get_db()
    conn.execute("DELETE FROM tem_sections")
    conn.executemany(
        "INSERT INTO tem_sections (heading, content) VALUES (:heading, :content)",
        sections
    )
    conn.execute(
        "INSERT OR REPLACE INTO resource_metadata VALUES ('tem', ?, ?)",
        (datetime.utcnow().isoformat(), TEM_URL)
    )
    conn.commit()
    conn.close()
    print(f"  Loaded into database.")


def _rebuild_gsm_fts():
    """Sync the FTS5 index from gsm_terms (active terms only)."""
    conn = get_db()
    conn.execute("DELETE FROM gsm_fts")
    conn.execute("""
        INSERT INTO gsm_fts(rowid, term, nice_class, notes)
        SELECT id, term, nice_class, notes FROM gsm_terms WHERE term_status = 1
    """)
    conn.commit()
    conn.close()
    print("  GSM FTS5 index rebuilt.")


def _is_boolean_query(query: str) -> bool:
    """Return True if the query contains boolean operators or FTS5 special syntax."""
    return bool(re.search(r'\b(AND|OR|NOT|and|or|not)\b|["\(\)\*]', query))


def _prep_fts_query(query: str) -> str:
    """Normalize user-entered boolean query for SQLite FTS5 (operators must be uppercase)."""
    query = re.sub(r'\band\b', 'AND', query, flags=re.IGNORECASE)
    query = re.sub(r'\bor\b',  'OR',  query, flags=re.IGNORECASE)
    query = re.sub(r'\bnot\b', 'NOT', query, flags=re.IGNORECASE)
    return query.strip()


def load_gsm():
    """Run the G&S Manual scraper, populate gsm_terms, then rebuild the FTS5 index."""
    from scrape_gsm import scrape as _scrape_gsm
    _scrape_gsm()
    _rebuild_gsm_fts()


def load_all():
    init_db()
    load_sggsm()
    load_tem()
    load_gsm()
    print("Done. CIPO resources ready.")


# ── Search API ─────────────────────────────────────────────────────────────────

def search_specificity(term: str, nice_class: str = None) -> list[dict]:
    """Find specificity guidance for a term, optionally filtered by Nice class."""
    conn = get_db()
    term_like = f"%{term.lower()}%"

    if nice_class:
        rows = conn.execute(
            """SELECT nice_class, term, guidance FROM specificity_guidelines
               WHERE (term LIKE ? OR guidance LIKE ?)
               AND nice_class = ?
               ORDER BY length(term) ASC LIMIT 20""",
            (term_like, term_like, nice_class.zfill(2))
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT nice_class, term, guidance FROM specificity_guidelines
               WHERE term LIKE ? OR guidance LIKE ?
               ORDER BY length(term) ASC LIMIT 20""",
            (term_like, term_like)
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def search_tem(query: str) -> list[dict]:
    """Find relevant TEM sections for a query."""
    conn = get_db()
    like = f"%{query.lower()}%"
    rows = conn.execute(
        """SELECT heading, content FROM tem_sections
           WHERE lower(heading) LIKE ? OR lower(content) LIKE ?
           LIMIT 5""",
        (like, like)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_gsm(term: str, nice_class: str = None, limit: int = 500) -> list[dict]:
    """
    Search the G&S Manual for pre-approved terms matching the query.
    Returns up to `limit` results within the specified class, then supplements
    with cross-class results if fewer than 30 class-specific matches are found.
    """
    conn = get_db()
    term_like = f"%{term.lower()}%"

    if nice_class:
        cls = nice_class.zfill(2)
        rows = conn.execute(
            """SELECT nice_class, term, term_status, notes
               FROM gsm_terms
               WHERE lower(term) LIKE ?
               AND nice_class = ?
               AND term_status = 1
               ORDER BY length(term) ASC LIMIT ?""",
            (term_like, cls, limit)
        ).fetchall()
        # If the class-specific result is sparse, also pull cross-class terms
        # so Claude has more options to consider
        if len(rows) < 30:
            extra = conn.execute(
                """SELECT nice_class, term, term_status, notes
                   FROM gsm_terms
                   WHERE lower(term) LIKE ?
                   AND nice_class != ?
                   AND term_status = 1
                   ORDER BY length(term) ASC LIMIT ?""",
                (term_like, cls, limit - len(rows))
            ).fetchall()
            rows = list(rows) + list(extra)
    else:
        rows = conn.execute(
            """SELECT nice_class, term, term_status, notes
               FROM gsm_terms
               WHERE lower(term) LIKE ?
               AND term_status = 1
               ORDER BY length(term) ASC LIMIT ?""",
            (term_like, limit)
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def search_gsm_fts(query: str, nice_class: str = None, limit: int = 500) -> list[dict]:
    """
    Boolean full-text search over the G&S Manual using SQLite FTS5.
    Supports AND, OR, NOT, "phrase matching", term* (prefix), and grouping with ().
    Falls back to LIKE search if the FTS5 query is malformed.
    """
    fts_query = _prep_fts_query(query)

    # Ensure FTS5 table exists and is populated
    conn = get_db()
    try:
        fts_count = conn.execute("SELECT COUNT(*) FROM gsm_fts").fetchone()[0]
    except Exception:
        fts_count = -1  # table doesn't exist yet — needs init_db + rebuild
    conn.close()
    if fts_count == -1:
        init_db()
        _rebuild_gsm_fts()
    elif fts_count == 0:
        _rebuild_gsm_fts()

    conn = get_db()
    try:
        if nice_class:
            cls = nice_class.zfill(2)
            rows = conn.execute(
                """SELECT nice_class, term, notes FROM gsm_fts
                   WHERE gsm_fts MATCH ? AND nice_class = ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, cls, limit),
            ).fetchall()
            if len(rows) < 30:
                extra = conn.execute(
                    """SELECT nice_class, term, notes FROM gsm_fts
                       WHERE gsm_fts MATCH ? AND nice_class != ?
                       ORDER BY rank LIMIT ?""",
                    (fts_query, cls, limit - len(rows)),
                ).fetchall()
                rows = list(rows) + list(extra)
        else:
            rows = conn.execute(
                """SELECT nice_class, term, notes FROM gsm_fts
                   WHERE gsm_fts MATCH ? ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        conn.close()
        return [{"nice_class": r[0], "term": r[1], "notes": r[2], "term_status": 1} for r in rows]
    except Exception:
        conn.close()
        # Malformed query — strip FTS5 special chars and fall back to LIKE
        clean = re.sub(r'["\(\)\*]', ' ', query).strip()
        return search_gsm(clean, nice_class, limit)


def gsm_loaded() -> bool:
    if not os.path.exists(DB_PATH):
        return False
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM gsm_terms").fetchone()[0]
    except Exception:
        count = 0
    conn.close()
    return count > 0


def get_metadata() -> dict:
    """Return download dates for each resource."""
    conn = get_db()
    rows = conn.execute("SELECT resource, downloaded FROM resource_metadata").fetchall()
    conn.close()
    return {r["resource"]: r["downloaded"] for r in rows}


def resources_loaded() -> bool:
    """Check if the core SGGSM resource has been loaded."""
    if not os.path.exists(DB_PATH):
        return False
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM specificity_guidelines").fetchone()[0]
    conn.close()
    return count > 0
