# Trademark Spec Tool

A tool for Canadian trademark lawyers and agents to automate specification amendments during the CIPO examination process.

## What it does

1. **Parse objections** — Upload a CIPO objection letter (Word .docx) and the app identifies which goods/services are objected to and why
2. **Propose amendments** — Suggests compliant replacement language drawn from CIPO's pre-approved list, examination manual, and the trademark register
3. **Cite every suggestion** — Every proposed amendment links back to its source so the reviewer can verify

## Project structure

```
trademark-spec-tool/
├── backend/        # Python (FastAPI) — API and AI logic
├── frontend/       # HTML/CSS/JS — user interface
```

## Setup

See `backend/README.md` for installation instructions.
