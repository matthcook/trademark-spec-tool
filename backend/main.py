import os
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from dotenv import load_dotenv

from doc_parser import parse_office_action
from cipo import fetch_application
from analyzer import analyze_office_action

load_dotenv()

app = FastAPI(
    title="Trademark Spec Tool",
    description="Automates CIPO trademark specification amendments",
    version="0.2.0",
)

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
        # Step 1: Parse the .docx
        parsed = parse_office_action(tmp_path)

        # Step 2: Fetch CIPO application data if we found an application number
        cipo_app = None
        cipo_error = None
        if parsed.application_number:
            try:
                cipo_app = fetch_application(parsed.application_number)
            except Exception as e:
                cipo_error = str(e)

        # Step 3: Analyze with Claude
        analysis = analyze_office_action(parsed, cipo_app)

        # Merge trademark/applicant from the document into the analysis
        if parsed.trademark_name and not analysis.get("trademark_name"):
            analysis["trademark_name"] = parsed.trademark_name
        if parsed.applicant_name and not analysis.get("applicant_name"):
            analysis["applicant_name"] = parsed.applicant_name

        return {
            "analysis": analysis,
            "cipo_fetch_error": cipo_error,
        }

    finally:
        os.unlink(tmp_path)


@app.post("/api/propose-amendments")
async def propose_amendments(file: UploadFile = File(...)):
    """Phase 4: Return amendment options with citations for each objected item."""
    return {"message": "Amendment engine coming in Phase 4"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
