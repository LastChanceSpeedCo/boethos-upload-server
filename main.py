"""
boethos_upload_server/main.py
==============================
Lightweight FastAPI server that receives run data uploads from the Boethos PC app.

Files are saved to ./uploads/<YYYYMMDD_HHMMSS_<uid>>/ on disk.

Deploy to Render.com:
  - Build command:  pip install -r requirements.txt
  - Start command:  uvicorn main:app --host 0.0.0.0 --port $PORT
  - Add a Persistent Disk mounted at /opt/render/project/src/uploads ($1/month)
    so files survive redeploys. Without it they land in ephemeral storage.

Environment variables (set in Render dashboard):
  UPLOAD_DIR   — optional override for uploads folder (default: ./uploads)
  API_KEY      — optional secret; if set, clients must send X-API-Key: <key>

Endpoint:
  POST /upload
    multipart/form-data:
      run_csv   (file)   trimmed telemetry CSV
      run_info  (file)   companion info .txt
      email     (str)    optional — for store credit tracking
      version   (str)    app version string
"""

from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
API_KEY    = os.environ.get("API_KEY", "")   # empty = no auth required

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("boethos-upload")

app = FastAPI(title="Boethos Upload Server", version="1.0.0")

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------
@app.post("/upload")
async def upload(
    run_csv:   UploadFile      = File(..., description="Trimmed run CSV"),
    run_info:  UploadFile      = File(..., description="Companion info .txt"),
    email:     str             = Form("",  description="Submitter email (optional)"),
    version:   str             = Form("",  description="Boethos app version"),
    x_api_key: str | None      = Header(None),
):
    # Auth
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    # Destination folder
    ts      = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    uid     = str(uuid.uuid4())[:8]
    run_dir = UPLOAD_DIR / f"{ts}_{uid}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save files
    csv_bytes  = await run_csv.read()
    info_bytes = await run_info.read()

    (run_dir / _safe(run_csv.filename  or "run.csv" )).write_bytes(csv_bytes)
    (run_dir / _safe(run_info.filename or "info.txt")).write_bytes(info_bytes)

    log.info(
        "Saved: id=%s_%s  csv=%dB  info=%dB  email=%s  ver=%s",
        ts, uid, len(csv_bytes), len(info_bytes),
        email or "none", version or "none",
    )

    return JSONResponse({
        "status":  "ok",
        "id":      f"{ts}_{uid}",
        "csv_kb":  round(len(csv_bytes)  / 1024, 1),
        "info_kb": round(len(info_bytes) / 1024, 1),
    })


def _safe(name: str) -> str:
    name = Path(name).name
    return "".join(c for c in name if c.isalnum() or c in "._- ")[:128] or "file"
