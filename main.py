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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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


def _check_auth(key: str | None) -> bool:
    """Returns True if auth passes, False if no API_KEY set, raises 401 if wrong key."""
    if not API_KEY:
        return True
    return key == API_KEY


# ---------------------------------------------------------------------------
# Browse uploads — browser login form
# ---------------------------------------------------------------------------
_LOGIN_PAGE = """<!DOCTYPE html>
<html><head><title>Boethos Uploads — Login</title>
<style>
  body {{ font-family: monospace; background: #0a0a0a; color: #ddd;
          display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .box {{ background: #111; border: 1px solid #2a2a2a; border-radius: 6px; padding: 40px; width: 340px; }}
  h2 {{ color: #00ff66; margin: 0 0 24px; }}
  input {{ width: 100%; box-sizing: border-box; background: #161616; color: #fff;
           border: 1px solid #2a2a2a; border-radius: 3px; padding: 8px 10px;
           font-family: monospace; font-size: 13px; margin-bottom: 16px; }}
  input:focus {{ outline: none; border-color: #00ff66; }}
  button {{ width: 100%; background: #1a3a2a; color: #00ff66; border: 1px solid #00ff66;
            border-radius: 3px; padding: 10px; font-family: monospace; font-weight: bold;
            cursor: pointer; font-size: 13px; }}
  button:hover {{ background: #2a5a3a; }}
  .err {{ color: #ff4040; margin-bottom: 12px; font-size: 12px; }}
</style></head>
<body><div class="box">
<h2>Boethos Uploads</h2>
{error}
<form method="get" action="/files">
  <input type="password" name="key" placeholder="API key" autofocus />
  <button type="submit">View Uploads</button>
</form>
</div></body></html>"""


@app.get("/files", response_class=HTMLResponse)
def list_files(key: str | None = None):
    # No key supplied — show login form
    if key is None:
        return HTMLResponse(_LOGIN_PAGE.format(error=""))

    # Wrong key — show login form with error
    if API_KEY and key != API_KEY:
        return HTMLResponse(
            _LOGIN_PAGE.format(error='<p class="err">Incorrect key.</p>'),
            status_code=401,
        )

    # Authenticated — build file listing
    if not UPLOAD_DIR.exists():
        runs = []
    else:
        runs = sorted(UPLOAD_DIR.iterdir(), reverse=True)

    rows = ""
    for run_dir in runs:
        if not run_dir.is_dir():
            continue
        for f in sorted(run_dir.iterdir()):
            size_kb = round(f.stat().st_size / 1024, 1)
            rows += (
                f'<tr><td>{run_dir.name}</td>'
                f'<td><a href="/files/{run_dir.name}/{f.name}?key={key}">{f.name}</a></td>'
                f'<td>{size_kb} KB</td></tr>\n'
            )

    html = f"""<!DOCTYPE html>
<html><head><title>Boethos Uploads</title>
<style>
  body {{ font-family: monospace; background: #0a0a0a; color: #ddd; padding: 32px; }}
  h1 {{ color: #00ff66; margin: 0 0 24px; }}
  a {{ color: #00bfff; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ color: #777; text-align: left; padding: 6px 16px 6px 0; border-bottom: 1px solid #2a2a2a; }}
  td {{ padding: 5px 16px 5px 0; }}
  tr:hover td {{ background: #111; }}
</style></head>
<body>
<h1>Boethos Run Uploads</h1>
<table><tr><th>Run ID</th><th>File</th><th>Size</th></tr>
{rows or '<tr><td colspan=3 style="color:#555">No uploads yet.</td></tr>'}
</table></body></html>"""
    return HTMLResponse(html)


@app.get("/files/{run_id}/{filename}")
def download_file(run_id: str, filename: str, key: str | None = None):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing key")

    path = UPLOAD_DIR / _safe(run_id) / _safe(filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path, filename=_safe(filename))
