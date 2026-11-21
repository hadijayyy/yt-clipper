"""Minimal web UI + JSON API: paste a YouTube link, get clips."""

from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from .config import ClipConfig
from .log import set_listener

STATIC = Path(__file__).parent / "static"


class JobRequest(BaseModel):
    url: str
    num_clips: int = Field(5, ge=1, le=20)
    min_duration: float = Field(20, ge=3, le=600)
    max_duration: float = Field(60, ge=3, le=900)
    aspect: str = "vertical"
    reframe: str = "blur"
    captions: bool = True
    hook_overlay: bool = True
    selector: str = "auto"
    transcript: str = "auto"
    languages: list[str] = ["id", "en"]
    ai_instructions: str = ""
    caption_highlight: str = "#FFD400"


class JobStore:
    def __init__(self, workers: int = 1):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        # Rendering is CPU-bound; one job at a time keeps each one fast.
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def submit(self, url: str, cfg: ClipConfig) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "url": url,
            "status": "queued",
            "created": time.time(),
            "logs": [],
            "manifest": None,
            "error": None,
        }
        with self.lock:
            self.jobs[job_id] = job
        self.pool.submit(self._run, job, cfg)
        return job

    def _run(self, job: dict, cfg: ClipConfig) -> None:
        from .pipeline import run_pipeline

        def on_log(msg: str) -> None:
            job["logs"].append(f"{time.strftime('%H:%M:%S')} {msg}")

        set_listener(on_log)
        job["status"] = "running"
        try:
            job["manifest"] = run_pipeline(job["url"], cfg)
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            on_log(f"ERROR: {e}")
            traceback.print_exc()
        finally:
            set_listener(None)


def create_app(output_dir: Path = Path("output"), work_dir: Path = Path("work")):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    store = JobStore()
    app = FastAPI(title="yt-clipper")

    def public(job: dict) -> dict:
        out = {k: job[k] for k in ("id", "url", "status", "created", "logs", "error")}
        m = job.get("manifest")
        if m:
            rel = Path(m["output_dir"]).resolve().relative_to(output_dir.resolve())
            base = f"/files/{rel.as_posix()}"
            out["result"] = {
                "title": m["title"],
                "duration": m["duration"],
                "transcript_source": m["transcript_source"],
                "elapsed_sec": m["elapsed_sec"],
                "clips": [
                    {
                        **c,
                        "video_url": f"{base}/{c['file']}",
                        "thumbnail_url": f"{base}/{c['thumbnail']}",
                        "srt_url": f"{base}/{c['srt']}" if c.get("srt") else None,
                    }
                    for c in m["clips"]
                ],
            }
        return out

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/jobs")
    def create_job(req: JobRequest):
        from .download import extract_video_id

        if not extract_video_id(req.url):
            raise HTTPException(400, "Link YouTube tidak valid.")
        cfg = ClipConfig(
            num_clips=req.num_clips,
            min_duration=req.min_duration,
            max_duration=req.max_duration,
            aspect=req.aspect,
            reframe=req.reframe,
            captions=req.captions,
            hook_overlay=req.hook_overlay,
            selector=req.selector,
            transcript=req.transcript,
            languages=req.languages or ["id", "en"],
            ai_instructions=req.ai_instructions[:1000],
            caption_highlight=req.caption_highlight,
            cookies=os.environ.get("YT_CLIPPER_COOKIES") or None,
            cookies_from_browser=os.environ.get("YT_CLIPPER_COOKIES_FROM_BROWSER") or None,
            work_dir=Path(work_dir),
            output_dir=output_dir,
        )
        try:
            cfg.validate()
        except ValueError as e:
            raise HTTPException(400, str(e))
        return public(store.submit(req.url.strip(), cfg))

    @app.get("/api/jobs")
    def list_jobs():
        jobs = sorted(store.jobs.values(), key=lambda j: j["created"], reverse=True)
        return [public(j) for j in jobs]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return public(job)

    app.mount("/files", StaticFiles(directory=output_dir), name="files")
    return app


def serve(host: str, port: int, output_dir: Path, work_dir: Path) -> None:
    import uvicorn

    print(f"yt-clipper UI: http://{host}:{port}")
    uvicorn.run(create_app(output_dir, work_dir), host=host, port=port, log_level="warning")
