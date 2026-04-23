import os
import re
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

from doc_parser import parse_office_action, extract_document_debug
from cipo import fetch_application
from analyzer import analyze_office_action, generate_amendment_suggestions, research_context
from cipo_resources import init_db, load_all, search_specificity, search_tem, search_gsm, get_metadata, resources_loaded, gsm_loaded

load_dotenv()

app = FastAPI(
    title="Trademark Spec Tool",
    description="Automates CIPO trademark specification amendments",
    version="0.3.0",
)


@app.on_event("startup")
async def startup_event():
    init_db()
    if not resources_loaded():
        import threading
        threading.Thread(target=load_all, daemon=True).start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(frontend_path, "index.html"))


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/application/{app_number}")
def lookup_application(app_number: str):
    """Fetch trademark application details from CIPO by application number."""
    try:
        app_data = fetch_application(app_number)
        return {
            "application_number": app_data.application_number,
            "trademark_name": app_data.trademark_name,
            "applicant": app_data.applicant,
            "status": app_data.status,
            "filing_date": app_data.filing_date,
            "specification": app_data.specification,
            "source_url": app_data.source_url,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch from CIPO: {str(e)}")


@app.post("/api/parse-objection")
async def parse_objection(file: UploadFile = File(...)):
    """
    Accept a CIPO office action (.docx), parse it, fetch the CIPO application,
    and return structured objection data.
    """
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Please upload a .docx (Word) file.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")

    # Save the uploaded file temporarily
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        import asyncio
        import traceback

        # Step 1: Parse the .docx
        parsed = parse_office_action(tmp_path)

        # Step 2: Fetch CIPO application data (run in thread — can block 20-30s with web search)
        cipo_app = None
        cipo_error = None
        cipo_spec_loaded = False

        numbers_to_try = [n for n in [parsed.application_number, parsed.ir_number] if n]
        if numbers_to_try:
            for number in numbers_to_try:
                try:
                    result = await asyncio.to_thread(fetch_application, number)
                    if result and result.specification:
                        cipo_app = result
                        cipo_spec_loaded = True
                        break
                    elif result and not cipo_app:
                        cipo_app = result
                except Exception as e:
                    cipo_error = str(e)
        else:
            cipo_error = "No application number or IR number found in document — could not fetch full specification from CIPO"

        # Step 3: Analyze with Claude (also run in thread)
        try:
            analysis = await asyncio.to_thread(analyze_office_action, parsed, cipo_app)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

        # Always use parser's application number — it reads tables that Claude never sees
        if parsed.application_number:
            analysis["application_number"] = parsed.application_number
        if parsed.trademark_name and not analysis.get("trademark_name"):
            analysis["trademark_name"] = parsed.trademark_name
        if parsed.applicant_name and not analysis.get("applicant_name"):
            analysis["applicant_name"] = parsed.applicant_name

        # Set a descriptive error if spec simply wasn't found (no exception thrown)
        if not cipo_spec_loaded and not cipo_error:
            num_tried = parsed.application_number or parsed.ir_number
            cipo_error = f"Specification not found on CIPO for {num_tried} — response may be incomplete"

        return {
            "analysis": analysis,
            "cipo_fetch_error": cipo_error,
            "cipo_spec_loaded": cipo_spec_loaded,
            "ir_number": parsed.ir_number,
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.get("/api/resources/status")
def resource_status():
    """Check whether CIPO reference resources are loaded."""
    meta = get_metadata()
    return {
        "loaded": resources_loaded(),
        "sggsm_downloaded": meta.get("sggsm"),
        "tem_downloaded": meta.get("tem"),
        "gsm_loaded": gsm_loaded(),
        "gsm_downloaded": meta.get("gsm"),
    }


class LoadSpecRequest(BaseModel):
    spec_text: str
    existing_analysis: dict


@app.post("/api/load-spec")
async def load_spec(body: LoadSpecRequest):
    """
    Merge a user-pasted CIPO specification into an existing analysis.
    Parses the spec text into classes and updates/adds entries in the analysis.
    """
    import asyncio
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")
    try:
        updated = await asyncio.to_thread(_merge_spec_into_analysis, body.spec_text, body.existing_analysis)
        return {"analysis": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spec merge failed: {str(e)}")


def _merge_spec_into_analysis(spec_text: str, analysis: dict) -> dict:
    """Use Claude to parse the pasted spec and merge it into the existing analysis."""
    import anthropic, json
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    existing_classes = json.dumps(analysis.get("classes", []), indent=2)

    prompt = f"""You are updating a trademark analysis with a complete specification from CIPO.

The existing analysis already has these classes (with objected terms identified):
{existing_classes}

The complete CIPO specification text (pasted by the user) is:
{spec_text}

Your task:
1. Parse the CIPO spec into classes (identified by "Class XX" headers or Nice class numbers)
2. For each class in the spec, check if it already exists in the existing analysis:
   - If YES: update its marked_text to use the FULL spec text for that class, keeping all existing {{{{objected_term}}}} markers intact
   - If NO: add it as a new entry with empty objected_terms array
3. Keep all existing objected_terms and their {{{{...}}}} markers exactly as-is
4. Return the complete updated classes array as JSON

Return ONLY a JSON array of class objects — no markdown, no explanation:
[
  {{
    "nice_class": "09",
    "goods_or_services": "Goods or Services",
    "marked_text": "full spec text with {{{{objected terms}}}} wrapped",
    "objected_terms": [...]
  }}
]"""

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
    updated_classes = json.loads(raw.strip())
    result = dict(analysis)
    result["classes"] = updated_classes
    return result


@app.post("/api/resources/reload")
def reload_resources():
    """Re-download and re-index all CIPO reference resources."""
    import threading
    threading.Thread(target=load_all, daemon=True).start()
    return {"message": "Resource download started in background."}


@app.get("/api/resources/search")
def search_resources(term: str, nice_class: str = None):
    """Search the Specificity Guidelines for a term."""
    results = search_specificity(term, nice_class)
    return {"term": term, "nice_class": nice_class, "results": results}


@app.get("/api/resources/search-gsm")
def search_gsm_terms(term: str, nice_class: str = None):
    """Search the pre-approved G&S Manual for matching terms."""
    results = search_gsm(term, nice_class)
    return {"term": term, "nice_class": nice_class, "results": results}


class ResearchRequest(BaseModel):
    applicant_name: str = ""
    trademark_name: str = ""


@app.post("/api/research-context")
async def research_context_endpoint(body: ResearchRequest):
    """Research the applicant's business and trademark use online."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")
    import asyncio
    try:
        result = await asyncio.to_thread(
            research_context, body.applicant_name, body.trademark_name
        )
    except Exception:
        result = {"blurb": None, "trademark_url": None}
    return result


class AmendmentRequest(BaseModel):
    term: str
    nice_class: str = ""
    reason: str = ""
    business_context: str = ""   # pre-loaded from /api/research-context


def _gsm_keywords(term: str) -> list[str]:
    """
    For complex multi-word phrases, extract multiple search keywords so GSM
    search returns useful results even when the full phrase has no exact match.
    E.g. "computer software for monitoring vehicle fleets" → also search
    "computer software", "computer", "vehicle", "fleet".
    """
    keywords = [term]
    words = term.strip().lower().split()
    if len(words) <= 2:
        return keywords
    # Take the noun phrase before the first purpose/use preposition
    head = re.split(r'\s+(?:for|used|to|of|including|relating|related|in|and)\b', term.lower(), maxsplit=1)[0].strip()
    if head and head != term.lower():
        keywords.append(head)
    # Always add first word and first two words as broad fallbacks
    keywords.append(words[0])
    if len(words) >= 3:
        keywords.append(" ".join(words[:2]))
    # Pick out any content words from the tail (after "for"/"in" etc.)
    stopwords = {"for","used","use","using","to","of","in","and","or","the",
                 "a","an","with","by","from","including","relating","related"}
    tail_words = [w for w in words if w not in stopwords and w not in keywords]
    if tail_words:
        keywords.append(tail_words[0])
    return list(dict.fromkeys(k for k in keywords if k))  # dedup, preserve order


@app.post("/api/suggest-amendments")
async def suggest_amendments(body: AmendmentRequest):
    """Return amendment options with citations for an objected term."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")

    import asyncio

    # Multi-keyword GSM search so complex phrases still get useful matches
    seen: set = set()
    all_gsm: list = []
    for kw in _gsm_keywords(body.term):
        for row in search_gsm(kw, body.nice_class or None):
            key = (row["term"].lower(), row.get("nice_class", ""))
            if key not in seen:
                seen.add(key)
                all_gsm.append(row)
        if len(all_gsm) >= 500:
            break
    gsm = all_gsm[:500]

    # Cap what goes to the AI prompt — the browse panel fetches live anyway,
    # so there's no reason to send 500 rows into a giant Claude prompt.
    # Prefer same-class terms, pad with cross-class up to the cap.
    nice_cls = body.nice_class.zfill(2) if body.nice_class else ""
    gsm_same  = [r for r in gsm if r.get("nice_class","").zfill(2) == nice_cls]
    gsm_cross = [r for r in gsm if r not in gsm_same]
    gsm_for_ai = (gsm_same[:140] + gsm_cross[:10])[:150]

    # Same multi-keyword approach for specificity guidelines
    sg_seen: set = set()
    all_sg: list = []
    for kw in _gsm_keywords(body.term)[:3]:
        for row in search_specificity(kw, body.nice_class or None):
            key = row["term"].lower()
            if key not in sg_seen:
                sg_seen.add(key)
                all_sg.append(row)
    sg = all_sg[:20]

    suggestion_error = None
    try:
        suggestions = await asyncio.to_thread(
            generate_amendment_suggestions,
            body.term, body.nice_class, body.reason, gsm_for_ai, sg,
            body.business_context,
        )
    except Exception as exc:
        suggestions = []
        suggestion_error = str(exc)

    return {
        "term": body.term,
        "nice_class": body.nice_class,
        "gsm_matches": gsm,
        "specificity_guidance": sg,
        "suggestions": suggestions,
        "suggestion_error": suggestion_error,
    }


@app.post("/api/debug-parse")
async def debug_parse(file: UploadFile = File(...)):
    """
    Return raw paragraph and table text extracted from a .docx without any
    analysis. Use this to diagnose why an application number or spec is not
    being found.
    """
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = extract_document_debug(tmp_path)
        # Also run the application number extractor so we can see what it finds
        parsed = parse_office_action(tmp_path)
        result["extracted_app_number"] = parsed.application_number
        result["extracted_trademark"] = parsed.trademark_name
        result["extracted_applicant"] = parsed.applicant_name
        return result
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
