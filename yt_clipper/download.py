"""Fetch a YouTube video, its metadata (heatmap, chapters) and subtitles via yt-dlp."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import ClipConfig
from .log import log

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def extract_video_id(url: str) -> str | None:
    m = YOUTUBE_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    return None


@dataclass
class SourceMedia:
    video_path: Path
    title: str = ""
    video_id: str = ""
    duration: float = 0.0
    url: str = ""
    subtitle_path: Path | None = None
    subtitle_lang: str | None = None
    heatmap: list[dict] = field(default_factory=list)  # [{start_time, end_time, value}]
    chapters: list[dict] = field(default_factory=list)  # [{start_time, end_time, title}]
    description: str = ""


def _ydl_base_opts(cfg: ClipConfig) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    if cfg.cookies:
        opts["cookiefile"] = cfg.cookies
    if cfg.cookies_from_browser:
        opts["cookiesfrombrowser"] = (cfg.cookies_from_browser,)
    # YouTube extraction needs a JS runtime; yt-dlp only enables deno by default.
    runtimes = {}
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            runtimes[name] = {}
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def _pick_subtitle(work: Path, video_id: str, langs: list[str]) -> tuple[Path | None, str | None]:
    files = sorted(work.glob(f"{video_id}*.vtt"))
    if not files:
        return None, None

    def lang_of(p: Path) -> str:
        # <id>.<lang>.vtt
        parts = p.name[len(video_id):].strip(".").split(".")
        return parts[0] if len(parts) >= 2 else ""

    by_lang = {lang_of(p): p for p in files}
    for want in langs:
        for lang, p in by_lang.items():
            if lang == want or lang.startswith(want + "-"):
                return p, lang
    p = files[0]
    return p, lang_of(p)


def download(url: str, cfg: ClipConfig) -> SourceMedia:
    """Download video + subtitles + metadata into cfg.work_dir/<video_id>/."""
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("yt-dlp is not installed: pip install yt-dlp") from e

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Not a recognizable YouTube URL: {url!r}")

    work = Path(cfg.work_dir) / video_id
    work.mkdir(parents=True, exist_ok=True)
    info_path = work / "info.json"

    sub_langs = []
    for lang in cfg.languages:
        sub_langs += [lang, f"{lang}-orig", f"{lang}.*"]
    want_subs = cfg.transcript in {"auto", "youtube"}

    opts = _ydl_base_opts(cfg)
    opts.update(
        {
            "outtmpl": str(work / "%(id)s.%(ext)s"),
            "format": (
                f"bv*[height<={cfg.max_height}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height<={cfg.max_height}]+ba/"
                f"b[height<={cfg.max_height}]/b"
            ),
            "merge_output_format": "mp4",
            "writesubtitles": want_subs,
            "writeautomaticsub": want_subs,
            "subtitleslangs": sub_langs,
            "subtitlesformat": "vtt",
            "ignoreerrors": False,
        }
    )

    existing = next((p for p in work.glob(f"{video_id}.mp4")), None)
    log(f"Fetching {url} ...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        if existing and info_path.exists():
            log("Video already downloaded, reusing cache.")
            info = json.loads(info_path.read_text())
        else:
            try:
                info = ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e:
                msg = str(e)
                if "Sign in to confirm" in msg or "429" in msg:
                    raise RuntimeError(
                        "YouTube blocked the download (bot check). Pass a cookies file "
                        "with --cookies cookies.txt or --cookies-from-browser chrome."
                    ) from e
                raise RuntimeError(f"Download failed: {msg}") from e
            info = ydl.sanitize_info(info)
            info_path.write_text(json.dumps(info, ensure_ascii=False))

    video_path = next(iter(sorted(work.glob(f"{video_id}.mp4"))), None) or next(
        (p for p in work.glob(f"{video_id}.*") if p.suffix in {".mkv", ".webm", ".mp4"}), None
    )
    if not video_path:
        raise RuntimeError("Download finished but no video file was found.")

    sub_path, sub_lang = _pick_subtitle(work, video_id, cfg.languages) if want_subs else (None, None)

    return SourceMedia(
        video_path=video_path,
        title=info.get("title") or video_id,
        video_id=video_id,
        duration=float(info.get("duration") or 0),
        url=info.get("webpage_url") or url,
        subtitle_path=sub_path,
        subtitle_lang=sub_lang,
        heatmap=info.get("heatmap") or [],
        chapters=info.get("chapters") or [],
        description=(info.get("description") or "")[:2000],
    )


def local_source(video: Path, subtitles: Path | None = None, title: str | None = None) -> SourceMedia:
    """Wrap a local file so the rest of the pipeline can run without YouTube."""
    from .media import probe_duration

    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    return SourceMedia(
        video_path=video,
        title=title or video.stem,
        video_id=re.sub(r"[^A-Za-z0-9_-]", "_", video.stem)[:40],
        duration=probe_duration(video),
        url=str(video),
        subtitle_path=Path(subtitles) if subtitles else None,
    )
