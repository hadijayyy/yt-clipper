"""End-to-end pipeline: link -> download -> transcript -> highlights -> rendered clips."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ClipConfig
from .download import SourceMedia, download
from .highlights import Clip, Signals, find_highlights
from .log import log
from .media import loudness_curve, require_ffmpeg
from .render import render_clip, slugify
from .transcript import load_transcript


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, words, clip: Clip, per_line: int = 6) -> None:
    from .captions import chunk_words
    from .transcript import Word

    rel = [Word(w.start - clip.start, w.end - clip.start, w.text) for w in words]
    lines = []
    for n, ch in enumerate(chunk_words(rel, per_line), 1):
        lines += [str(n), f"{_srt_time(ch[0].start)} --> {_srt_time(ch[-1].end)}",
                  " ".join(w.text for w in ch), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(source: str | SourceMedia, cfg: ClipConfig | None = None) -> dict:
    cfg = cfg or ClipConfig()
    cfg.validate()
    require_ffmpeg()
    t0 = time.time()

    src = source if isinstance(source, SourceMedia) else download(source, cfg)
    if not src.duration:
        from .media import probe_duration
        src.duration = probe_duration(src.video_path)
    log(f"Source: {src.title!r} ({src.duration:.0f}s)")

    if src.duration < cfg.min_duration:
        raise ValueError(
            f"Video is {src.duration:.0f}s long, shorter than min clip duration {cfg.min_duration:.0f}s."
        )

    transcript = load_transcript(src, cfg)

    log("Analyzing audio energy ...")
    loud = loudness_curve(src.video_path)
    sig = Signals(src.duration, src.heatmap, loud)
    if sig.has_heat:
        log("Using YouTube 'most replayed' heatmap.")

    meta = {
        "title": src.title,
        "duration": src.duration,
        "description": src.description,
        "chapters": src.chapters,
    }
    clips = find_highlights(transcript, sig, meta, cfg)
    if not clips:
        raise RuntimeError("No clips found.")
    log(f"Selected {len(clips)} clips.")

    out_dir = Path(cfg.output_dir) / src.video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, clip in enumerate(clips, 1):
        name = f"clip_{i:02d}_{slugify(clip.title)}"
        log(f"Rendering {i}/{len(clips)}: {clip.title!r} "
            f"[{clip.start:.1f}s - {clip.end:.1f}s, {clip.duration:.1f}s]")
        files = render_clip(src.video_path, clip, transcript, out_dir / f"{name}.mp4", cfg)
        words = transcript.words_between(clip.start, clip.end)
        srt = None
        if words:
            srt = out_dir / f"{name}.srt"
            write_srt(srt, words, clip)
        entry = clip.to_dict()
        entry.update(
            index=i,
            file=files["video"].name,
            thumbnail=files["thumbnail"].name,
            srt=srt.name if srt else None,
            width=files["width"],
            height=files["height"],
            source_url=_timestamp_url(src, clip.start),
        )
        results.append(entry)

    manifest = {
        "title": src.title,
        "video_id": src.video_id,
        "url": src.url,
        "duration": src.duration,
        "transcript_source": transcript.source,
        "selector": cfg.selector,
        "config": cfg.to_dict(),
        "output_dir": str(out_dir),
        "elapsed_sec": round(time.time() - t0, 1),
        "clips": results,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log(f"Done in {manifest['elapsed_sec']}s -> {out_dir}")
    return manifest


def _timestamp_url(src: SourceMedia, start: float) -> str:
    if src.url.startswith("http") and "youtu" in src.url:
        return f"https://youtu.be/{src.video_id}?t={int(start)}"
    return src.url
