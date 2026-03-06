"""FastAPI backend for SlideScholar."""

import io
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from pdf_parser import parse_pdf
from slide_builder import build_presentation
from slide_planner import plan_slides

load_dotenv()

# --- Config ---
TEMP_ROOT = Path("/tmp/slidescholar")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
CLEANUP_AGE_SECONDS = 86400  # 24 hours
ARXIV_PDF_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+(?:v\d+)?)")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("slidescholar")

# --- App ---
app = FastAPI(title="SlideScholar", version="0.1.0")

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-memory session store ---
# Maps paper_id -> {"parsed": dict, "session_dir": str}
_sessions: dict[str, dict] = {}


# --- Request / response models ---

class GenerateRequest(BaseModel):
    paper_id: str
    talk_length: str = "conference"
    include_speaker_notes: bool = True
    include_backup_slides: bool = True

    @field_validator("talk_length")
    @classmethod
    def validate_talk_length(cls, v: str) -> str:
        allowed = {"lightning", "short", "conference", "extended",
                   "invited", "seminar", "defense"}
        if v not in allowed:
            raise ValueError(f"talk_length must be one of {allowed}")
        return v


class ArxivRequest(BaseModel):
    arxiv_url: str

    @field_validator("arxiv_url")
    @classmethod
    def validate_arxiv_url(cls, v: str) -> str:
        if not ARXIV_PDF_RE.search(v):
            raise ValueError(
                "Invalid arXiv URL. Expected format: https://arxiv.org/abs/2301.12345"
            )
        return v


# --- Helpers ---

def _cleanup_old_sessions():
    """Delete session directories older than CLEANUP_AGE_SECONDS.

    Uses the most recent access time of any file in the directory,
    so active sessions (downloads, rebuilds) are not prematurely cleaned.
    """
    if not TEMP_ROOT.exists():
        return
    now = time.time()
    removed = 0
    for entry in TEMP_ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            files = [f for f in entry.iterdir() if f.is_file()]
            if files:
                latest_access = max(f.stat().st_atime for f in files)
            else:
                latest_access = entry.stat().st_mtime
        except OSError:
            latest_access = entry.stat().st_mtime
        if (now - latest_access) > CLEANUP_AGE_SECONDS:
            shutil.rmtree(entry, ignore_errors=True)
            _sessions.pop(entry.name, None)
            removed += 1
    if removed:
        log.info("Cleaned up %d expired session(s)", removed)


def _create_session_dir() -> tuple[str, Path]:
    """Create a unique session directory and return (paper_id, path)."""
    paper_id = uuid.uuid4().hex
    session_dir = TEMP_ROOT / paper_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return paper_id, session_dir


def _download_arxiv_pdf(arxiv_url: str, dest_path: Path) -> Path:
    """Download a PDF from arXiv to dest_path."""
    match = ARXIV_PDF_RE.search(arxiv_url)
    if not match:
        raise ValueError("Could not extract arXiv paper ID")

    paper_id = match.group(1)
    pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"

    log.info("Downloading arXiv PDF: %s", pdf_url)
    resp = requests.get(pdf_url, timeout=60, stream=True, headers={
        "User-Agent": "SlideScholar/0.1 (academic-tool)"
    })
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "pdf" not in content_type and not resp.content[:5] == b"%PDF-":
        raise ValueError(f"arXiv did not return a PDF (content-type: {content_type})")

    pdf_path = dest_path / "paper.pdf"
    with open(pdf_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    return pdf_path


# --- Health check ---

@app.get("/health")
def health():
    return {"status": "ok"}


# --- Middleware ---

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    log.info("%s %s → %d (%.0fms)", request.method, request.url.path, response.status_code, elapsed)
    return response


# --- Endpoints ---

@app.post("/api/parse")
async def parse_pdf_upload(file: UploadFile = File(...)):
    """Upload a PDF and parse its structure."""
    _cleanup_old_sessions()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # Read with size check
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    paper_id, session_dir = _create_session_dir()
    pdf_path = session_dir / "paper.pdf"
    pdf_path.write_bytes(contents)

    try:
        parsed = parse_pdf(str(pdf_path), image_output_dir=str(session_dir / "figures"))
    except (FileNotFoundError, ValueError) as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {exc}")
    except Exception as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        log.exception("Unexpected error parsing PDF")
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {exc}")

    _sessions[paper_id] = {"parsed": parsed, "session_dir": str(session_dir)}

    # Build response
    return {
        "paper_id": paper_id,
        "title": parsed["title"],
        "authors": parsed["authors"],
        "abstract": parsed["abstract"][:500] if parsed["abstract"] else "",
        "num_pages": parsed["num_pages"],
        "num_figures": parsed["num_figures"],
        "sections": [
            {"name": s["name"], "text_preview": s["text"][:200] + "…" if len(s["text"]) > 200 else s["text"]}
            for s in parsed["sections"]
        ],
        "figures": [
            {
                "url": f"/api/figures/{paper_id}/{fig['filename']}",
                "figure_label": fig.get("figure_label") or f"Figure {fig.get('figure_number') or (i + 1)}",
                "caption": fig.get("caption", ""),
                "page": fig.get("page", 0),
            }
            for i, fig in enumerate(parsed.get("figures", []))
        ],
    }


@app.post("/api/parse-arxiv")
async def parse_arxiv(body: ArxivRequest):
    """Download a PDF from arXiv and parse it."""
    _cleanup_old_sessions()

    paper_id, session_dir = _create_session_dir()

    try:
        pdf_path = _download_arxiv_pdf(body.arxiv_url, session_dir)
    except requests.RequestException as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=f"Failed to download from arXiv: {exc}")
    except ValueError as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        parsed = parse_pdf(str(pdf_path), image_output_dir=str(session_dir / "figures"))
    except (FileNotFoundError, ValueError) as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {exc}")
    except Exception as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        log.exception("Unexpected error parsing arXiv PDF")
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {exc}")

    _sessions[paper_id] = {"parsed": parsed, "session_dir": str(session_dir)}

    return {
        "paper_id": paper_id,
        "title": parsed["title"],
        "authors": parsed["authors"],
        "abstract": parsed["abstract"][:500] if parsed["abstract"] else "",
        "num_pages": parsed["num_pages"],
        "num_figures": parsed["num_figures"],
        "sections": [
            {"name": s["name"], "text_preview": s["text"][:200] + "…" if len(s["text"]) > 200 else s["text"]}
            for s in parsed["sections"]
        ],
        "figures": [
            {
                "url": f"/api/figures/{paper_id}/{fig['filename']}",
                "figure_label": fig.get("figure_label") or f"Figure {fig.get('figure_number') or (i + 1)}",
                "caption": fig.get("caption", ""),
                "page": fig.get("page", 0),
            }
            for i, fig in enumerate(parsed.get("figures", []))
        ],
    }


@app.post("/api/generate")
async def generate_slides(body: GenerateRequest):
    """Generate a .pptx presentation from a previously parsed paper."""
    session = _sessions.get(body.paper_id)
    if not session:
        raise HTTPException(status_code=404, detail="Paper not found. Upload or parse a PDF first.")

    parsed = session["parsed"]
    session_dir = session["session_dir"]

    try:
        plan = plan_slides(
            paper=parsed,
            talk_length=body.talk_length,
            include_speaker_notes=body.include_speaker_notes,
            include_backup_slides=body.include_backup_slides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Slide planning failed: {exc}")

    # Build the .pptx
    file_id = uuid.uuid4().hex
    output_path = os.path.join(session_dir, f"{file_id}.pptx")

    try:
        # Use Claude's extracted authors if available, fall back to parser's
        if not plan.get("authors") or plan["authors"] == "Unknown":
            plan["authors"] = parsed.get("authors", "")
        build_presentation(
            slide_plan=plan,
            figures=parsed.get("figures", []),
            output_path=output_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Slide building failed: {exc}")

    # Store the file_id -> path mapping in the session
    session.setdefault("files", {})[file_id] = output_path

    return {
        "download_url": f"/api/download/{file_id}",
        "slide_plan": plan,
    }


@app.get("/api/figures/{paper_id}/{filename}")
async def get_figure(paper_id: str, filename: str):
    """Serve an extracted figure image."""
    session = _sessions.get(paper_id)
    if not session:
        raise HTTPException(status_code=404, detail="Paper not found")

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = Path(session["session_dir"]) / "figures" / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Figure not found")

    suffix = file_path.suffix.lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix.lstrip("."), "image/png"
    )
    return FileResponse(path=str(file_path), media_type=media)


@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    """Download a generated .pptx file."""
    # Search all sessions for the file_id
    for session in _sessions.values():
        file_path = session.get("files", {}).get(file_id)
        if file_path and os.path.isfile(file_path):
            return FileResponse(
                path=file_path,
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                filename="slidescholar_presentation.pptx",
            )

    raise HTTPException(status_code=404, detail="File not found or expired")


@app.post("/api/rebuild")
async def rebuild_presentation(request: Request):
    """Rebuild .pptx from a modified slide plan and stream it directly.

    This avoids temp-file expiration issues: the file is built in a
    temporary location, read into memory, and streamed in one request.
    """
    data = await request.json()
    slide_plan = data.get("slide_plan")
    paper_id = data.get("paper_id")

    if not slide_plan:
        raise HTTPException(status_code=400, detail="No slide_plan provided")

    # Look up session for figures
    session = _sessions.get(paper_id) if paper_id else None
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session expired. Please go back and re-upload/parse your paper.",
        )

    parsed = session["parsed"]

    # Use Claude's extracted authors if available, fall back to parser's
    if not slide_plan.get("authors") or slide_plan["authors"] == "Unknown":
        slide_plan["authors"] = parsed.get("authors", "")

    # Build into a temp file, then stream the bytes directly
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        build_presentation(
            slide_plan=slide_plan,
            figures=parsed.get("figures", []),
            output_path=tmp_path,
        )
        with open(tmp_path, "rb") as f:
            content = f.read()
    except Exception as exc:
        log.exception("Rebuild failed")
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {exc}")
    finally:
        # Clean up temp file immediately — bytes are already in memory
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    buffer = io.BytesIO(content)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": 'attachment; filename="slidescholar_presentation.pptx"',
        },
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
