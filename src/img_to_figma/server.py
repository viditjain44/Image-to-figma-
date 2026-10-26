"""FastAPI server used by the web UI and the Figma plugin.

Conversions run one at a time in a background thread (the models share one GPU/RAM),
so uploads queue up instead of fighting over memory.
"""
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config, pipeline
from .schemas import Document

app = FastAPI(title="img-to-figma")
# Figma plugin iframes send requests with Origin "null"; the Vite dev server runs on another port.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=config.OUTPUT_DIR), name="files")

_worker = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _run(job_id: str, path: Path, opts: pipeline.Options, name: str) -> None:
    job = _jobs[job_id]

    def progress(msg: str) -> None:
        with _lock:
            job["log"].append(msg)

    job["status"] = "running"
    try:
        job["document"] = pipeline.run(path, opts, job_id=job_id, source_name=name, progress=progress).model_dump()
        job["status"] = "done"
    except Exception as e:
        job["status"], job["error"] = "error", str(e)
        progress(traceback.format_exc())


@app.get("/", response_class=HTMLResponse)
def home():
    """People open this port by mistake: point them to the editor."""
    return """<!doctype html><meta charset="utf-8"><title>img-to-figma API</title>
<body style="font:15px system-ui;max-width:520px;margin:15vh auto;padding:0 16px">
<h2>img-to-figma API is running ✓</h2>
<p>This port serves the editor and the Figma plugin. Open the editor at
<a href="http://localhost:5173">http://localhost:5173</a>.</p>
<p style="color:#888">Not loading? Start it with <code>cd web && npm run dev</code>
(or ⌘⇧B in VS Code).</p></body>"""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/defaults")
def defaults():
    return pipeline.Options().model_dump()


@app.post("/convert")
async def convert(image: UploadFile = File(...), options: str = Form("{}")):
    name = image.filename or "image.png"
    ext = Path(name).suffix.lower() or ".png"
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, f"Unsupported file type {ext}")
    opts = pipeline.Options.model_validate(json.loads(options or "{}"))
    job_id = pipeline.new_job_id()
    folder = config.OUTPUT_DIR / job_id
    folder.mkdir(parents=True)
    path = folder / f"upload{ext}"
    path.write_bytes(await image.read())
    queued = sum(j["status"] in ("queued", "running") for j in _jobs.values())
    _jobs[job_id] = {"id": job_id, "status": "queued", "name": name, "log": [], "document": None, "error": ""}
    _worker.submit(_run, job_id, path, opts, name)
    return {"job_id": job_id, "position_in_queue": queued}


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id in _jobs:
        return _jobs[job_id]
    saved = config.OUTPUT_DIR / job_id / "document.json"  # finished before a server restart
    if saved.exists():
        doc = json.loads(saved.read_text())
        return {"id": job_id, "status": "done", "name": doc["source_name"], "log": [], "document": doc, "error": ""}
    raise HTTPException(404, "job not found")


@app.get("/jobs")
def jobs(limit: int = 30):
    """Finished conversions, newest first."""
    out = []
    for p in sorted(config.OUTPUT_DIR.glob("*/document.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        doc = json.loads(p.read_text())
        out.append({"job_id": doc["job_id"], "name": doc["source_name"], "width": doc["width"],
                    "height": doc["height"], "layers": len(doc["layers"]),
                    "thumb": doc["files"]["original"]})
    return out


@app.put("/jobs/{job_id}/document")
def save_document(job_id: str, doc: Document):
    """Save edits made in the web UI (text, sizes, colours, visibility)."""
    path = config.OUTPUT_DIR / job_id / "document.json"
    if not path.exists() or doc.job_id != job_id:
        raise HTTPException(404, "job not found")
    path.write_text(doc.model_dump_json(indent=2))
    if job_id in _jobs:
        _jobs[job_id]["document"] = doc.model_dump()
    return {"ok": True}
