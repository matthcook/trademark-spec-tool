# Trademark Spec Tool — Engineer Setup Guide

A tool for Canadian trademark lawyers and agents to automate specification amendments during CIPO examination. Built with Python (FastAPI) backend and plain HTML/JS frontend.

---

## Prerequisites

- Python 3.9 or later (`python3 --version`)
- Git
- An Anthropic API key (get one at console.anthropic.com)

---

## 1. Clone the repository

```bash
git clone https://github.com/matthcook/trademark-spec-tool.git
cd trademark-spec-tool
```

---

## 2. Set up the Python environment

```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

---

## 3. Configure the API key

Create a file called `.env` inside the `backend/` folder:

```bash
echo "ANTHROPIC_API_KEY=your-key-here" > backend/.env
```

Replace `your-key-here` with the actual Anthropic API key. The `.env` file is git-ignored and must never be committed.

---

## 4. Start the server

```bash
cd backend
venv/bin/python main.py
```

The server starts on port 8000. Open `http://localhost:8000` in a browser to use the tool.

On first startup, the app downloads CIPO reference data in the background (the Goods & Services Manual and Specificity Guidelines). This can take a few minutes. The tool is usable immediately but amendment suggestions may be limited until the download completes. Status is shown in the UI.

---

## 5. Verify it's running

```bash
curl http://localhost:8000/api/health
# Should return: {"status":"ok","version":"0.2.0"}
```

---

## Project structure

```
trademark-spec-tool/
├── backend/
│   ├── main.py            # FastAPI app, all HTTP endpoints
│   ├── analyzer.py        # All Claude API calls (analysis, amendments, grammar)
│   ├── doc_parser.py      # Parses CIPO office action .docx files
│   ├── cipo.py            # Fetches trademark application data from CIPO website
│   ├── cipo_resources.py  # Downloads and indexes CIPO reference data (GSM, TEM, etc.)
│   ├── scrape_gsm.py      # Scraper for the CIPO Goods & Services Manual
│   ├── requirements.txt   # Python dependencies
│   ├── data/
│   │   ├── cipo.db        # SQLite database (CIPO reference data + user spec library)
│   │   └── gsm_raw.txt    # Raw GSM data cache
│   └── .env               # API key (not in git — must be created manually)
└── frontend/
    └── index.html         # Single-page frontend (HTML/CSS/JS, no build step)
```

---

## How the tool works

1. User uploads a CIPO office action (.docx)
2. The app parses the Word document, fetches the application record from CIPO, and calls Claude to identify objected goods/services
3. For each objected term, the user can request amendment suggestions — Claude searches the CIPO Goods & Services Manual and Specificity Guidelines, then ranks the best replacements
4. A grammar/duplicate checker reviews the full specification before the user finalizes

Each of steps 2–4 makes one or more calls to the Anthropic API (Claude Sonnet model).

---

## Keeping dependencies up to date

```bash
cd backend
venv/bin/pip install --upgrade -r requirements.txt
```

To upgrade the Anthropic SDK specifically:
```bash
venv/bin/pip install --upgrade anthropic
```

After upgrading, restart the server.

---

## CIPO reference data

The app downloads three CIPO datasets on startup and refreshes them every 30 days automatically:
- **Goods & Services Manual (GSM)** — pre-approved trademark terms
- **Specificity Guidelines** — CIPO guidance on acceptable specificity
- **TEM** — Trademark Examination Manual

These are stored in `backend/data/cipo.db`. If the database becomes corrupted, delete it and restart — it will be rebuilt from scratch.

---

## Deploying for multiple users

The app is a standard FastAPI/Python web server and can be hosted anywhere Python runs.

**Recommended hosting options (in order of simplicity):**
- [Render](https://render.com) — easiest, free tier available, auto-deploys from GitHub
- [Railway](https://railway.app) — similar to Render
- DigitalOcean / AWS EC2 — more control, more setup

**Key deployment notes:**
- Set `ANTHROPIC_API_KEY` as an environment variable on the host (not via `.env` file)
- The `data/` directory must be persistent storage — if the host uses ephemeral containers, mount a persistent volume at `backend/data/`
- The app serves the frontend from FastAPI itself — no separate web server needed
- For multiple concurrent users, run with a production server: `venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4`
- Add authentication (e.g. a reverse proxy with basic auth, or a login system) before exposing to the internet

---

## API cost monitoring

All API usage and costs are visible at console.anthropic.com → Usage. You can set spend limits under Account → Limits to avoid unexpected charges.

---

## Troubleshooting

**Server won't start:**
- Check that `.env` exists in the `backend/` folder and contains a valid `ANTHROPIC_API_KEY`
- Confirm Python 3.9+ is installed: `python3 --version`
- Confirm dependencies are installed: `venv/bin/pip list | grep fastapi`

**Amendment suggestions are empty:**
- CIPO reference data may still be loading — check `http://localhost:8000/api/resources/status`
- If `loaded: false` after 5+ minutes, restart the server

**"ANTHROPIC_API_KEY is not configured" error:**
- The `.env` file is missing or in the wrong location — it must be inside `backend/`, not the project root

**Office action not parsing correctly:**
- Use the debug endpoint: `POST /api/debug-parse` with the .docx file — returns raw extracted text to diagnose parsing issues
