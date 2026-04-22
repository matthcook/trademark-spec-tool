from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="Trademark Spec Tool",
    description="Automates CIPO trademark specification amendments",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(frontend_path, "index.html"))


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}


# --- Placeholder endpoints (to be built out in later phases) ---

@app.post("/api/parse-objection")
async def parse_objection(file: UploadFile = File(...)):
    """Phase 2: Accept a CIPO objection letter (.docx) and extract objected goods/services."""
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Please upload a .docx (Word) file.")
    return {"message": "Objection parser coming in Phase 2", "filename": file.filename}


@app.post("/api/propose-amendments")
async def propose_amendments(file: UploadFile = File(...)):
    """Phase 4: Return amendment options with citations for each objected item."""
    return {"message": "Amendment engine coming in Phase 4"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
