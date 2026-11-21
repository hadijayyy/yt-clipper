"""Two-step workflow for an outside decision maker (e.g. Claude Code via the skill).

1. ``prepare``: download + transcript + signals + frame contact sheets, written as
   files an agent can read (``transcript.txt``, ``overview.md``, ``frames/*.jpg``).
2. The agent picks clips and writes a plan (JSON).
3. ``render_plan``: render exactly the clips in the plan.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from .config import ClipConfig
from .download import SourceMedia, download
from .highlights import Clip, Signals, heuristic_select
from .log import log
from .media import loudness_curve, probe_duration, require_ffmpeg, run
from .transcript import Transcript, Word, build_segments, load_transcript

ANALYSIS_DIR = "analysis"


def _fmt(t: float) -> str:
    t = max(0, int(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ------------------------------------------------------------------ contact sheets

def contact_sheet(
    video: Path,
    times: list[float],
    out_path: Path,
    cols: int = 4,
    thumb_width: int = 384,
) -> Path:
    """Grab one frame per timestamp (fast seeks) and tile them, each labelled with its time."""
    out_path = Path(out_path)
    tmp = out_path.parent / f".{out_path.stem}_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    label = True
    for i, t in enumerate(times):
        frame = tmp / f"f{i:03d}.jpg"
        base = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                "-frames:v", "1", "-q:v", "4"]
        vf = f"scale={thumb_width}:-2"
        if label:
            text = _fmt(t).replace(":", r"\:")
            vf_l = (vf + f",drawtext=text='{text}':x=8:y=8:fontsize={thumb_width // 14}:"
                    "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6")
            try:
                run(base + ["-vf", vf_l, str(frame)])
                continue
            except RuntimeError:
                label = False  # ffmpeg without drawtext/fonts: unlabeled frames
        run(base + ["-vf", vf, str(frame)])
    frames = sorted(tmp.glob("f*.jpg"))
    if not frames:
        raise RuntimeError("Could not extract any frames.")
    rows = math.ceil(len(frames) / cols)
    run([
        "ffmpeg", "-hide_banner", "-y", "-loglevel", "error",
        "-framerate", "1", "-i", str(tmp / "f%03d.jpg"),
        "-vf", f"tile={cols}x{rows}:padding=4:margin=4:color=black",
        "-frames:v", "1", "-q:v", "3", str(out_path),
    ])
    for f in frames:
        f.unlink()
    tmp.rmdir()
    return out_path


def overview_times(duration: float, max_frames: int = 96, min_interval: float = 10.0) -> list[float]:
    interval = max(min_interval, duration / max_frames)
    n = max(1, int(duration // interval))
    return [min(duration - 0.5, interval / 2 + k * interval) for k in range(n)]


# ------------------------------------------------------------------------ prepare

def prepare(source: str | SourceMedia, cfg: ClipConfig | None = None, sheets: bool = True) -> Path:
    """Download and analyze a video. Returns the analysis directory."""
    cfg = cfg or ClipConfig()
    require_ffmpeg()
    src = source if isinstance(source, SourceMedia) else download(source, cfg)
    if not src.duration:
        src.duration = probe_duration(src.video_path)
    out = Path(cfg.work_dir) / src.video_id / ANALYSIS_DIR
    out.mkdir(parents=True, exist_ok=True)
    log(f"Analyzing {src.title!r} ({_fmt(src.duration)}) -> {out}")

    tr = load_transcript(src, cfg)
    loud = loudness_curve(src.video_path)
    sig = Signals(src.duration, src.heatmap, loud)

    # Transcript with numbered segments: the agent references these numbers in its plan.
    lines = [f"[{i}] {s.start:.1f}-{s.end:.1f} ({_fmt(s.start)}) {s.text}" for i, s in enumerate(tr.segments)]
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "words.json").write_text(
        json.dumps([[round(w.start, 3), round(w.end, 3), w.text] for w in tr.words], ensure_ascii=False),
        encoding="utf-8",
    )

    # Heuristic shortlist: a starting point, not the answer.
    shortlist_cfg = replace(cfg, num_clips=max(8, cfg.num_clips * 2))
    shortlist = heuristic_select(tr, sig, src.duration, shortlist_cfg) if src.duration >= cfg.min_duration else []

    sheet_files: list[str] = []
    if sheets:
        times = overview_times(src.duration)
        per_sheet = 16
        frames_dir = out / "frames"
        frames_dir.mkdir(exist_ok=True)
        for k in range(0, len(times), per_sheet):
            chunk = times[k:k + per_sheet]
            p = frames_dir / f"overview_{k // per_sheet + 1:02d}_{_fmt(chunk[0]).replace(':', '-')}.jpg"
            contact_sheet(src.video_path, chunk, p)
            sheet_files.append(str(p.relative_to(out)))
        log(f"Wrote {len(sheet_files)} contact sheet(s).")

    state = {
        "video_id": src.video_id,
        "title": src.title,
        "url": src.url,
        "duration": src.duration,
        "video_path": str(Path(src.video_path).resolve()),
        "transcript_source": tr.source,
        "language": tr.language or src.subtitle_lang,
        "chapters": src.chapters,
        "heatmap": src.heatmap,
        "sheets": sheet_files,
    }
    (out / "analysis.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "overview.md").write_text(_overview_md(state, tr, sig, shortlist), encoding="utf-8")
    log(f"Ready: {out}")
    return out


def _overview_md(state: dict, tr: Transcript, sig: Signals, shortlist: list[Clip]) -> str:
    md = [f"# {state['title']}", "",
          f"- URL: {state['url']}",
          f"- Duration: {_fmt(state['duration'])} ({state['duration']:.0f} s)",
          f"- Transcript: {state['transcript_source']} ({len(tr.segments)} segments, "
          f"{len(tr.words)} words, language {state.get('language') or 'unknown'})",
          f"- Frame contact sheets: {len(state['sheets'])} in frames/ (each tile is labelled with its timestamp)",
          ""]
    if state["chapters"]:
        md += ["## Chapters", ""]
        md += [f"- {_fmt(c.get('start_time', 0))} {c.get('title', '')}" for c in state["chapters"]]
        md.append("")
    if sig.has_heat:
        md += ["## YouTube 'most replayed' peaks", "",
               "Strongest audience signal available. Value 1.0 = most replayed part of the video.", ""]
        seen: list[int] = []
        for s in sorted(range(sig.n), key=lambda s: sig.heat[s], reverse=True):
            if all(abs(s - p) > 20 for p in seen):
                seen.append(s)
                md.append(f"- {_fmt(s)} ({s} s): {sig.heat[s]:.2f}")
            if len(seen) >= 10:
                break
        md.append("")
    if sig.has_loud and max(sig.loud, default=0) >= 0.3:
        md += ["## Loudest moments (laughter, shouting, music, applause)", ""]
        seen = []
        for s in sorted(range(sig.n), key=lambda s: sig.loud[s], reverse=True):
            if sig.loud[s] < 0.3:  # only clearly louder than the typical level
                break
            if all(abs(s - p) > 20 for p in seen):
                seen.append(s)
                md.append(f"- {_fmt(s)} ({s} s)")
            if len(seen) >= 8:
                break
        md.append("")
    if shortlist:
        md += ["## Heuristic shortlist (signal-based starting point; judge the content yourself)", "",
               "| # | start-end | score | opening words |", "|---|---|---|---|"]
        for i, c in enumerate(shortlist, 1):
            words = " ".join(c.text.split()[:14]).replace("|", "/")
            md.append(f"| {i} | {c.start:.1f}-{c.end:.1f} ({_fmt(c.start)}) | {c.score:.0f} | {words} |")
        md.append("")
    return "\n".join(md)


# ------------------------------------------------------------------------- frames

def range_sheet(analysis_dir: Path, start: float, end: float, count: int = 12) -> Path:
    """Contact sheet of a time range, for a closer look at a candidate clip."""
    analysis_dir = Path(analysis_dir)
    state = json.loads((analysis_dir / "analysis.json").read_text(encoding="utf-8"))
    end = min(end, state["duration"] - 0.1)
    if end <= start:
        raise ValueError("end must be after start")
    count = max(2, min(count, 24))
    step = (end - start) / count
    times = [start + step * (k + 0.5) for k in range(count)]
    out = analysis_dir / "frames" / f"range_{int(start)}-{int(end)}.jpg"
    out.parent.mkdir(exist_ok=True)
    return contact_sheet(Path(state["video_path"]), times, out)


# -------------------------------------------------------------------- render plan

def load_prepared(analysis_dir: Path) -> tuple[dict, SourceMedia, Transcript]:
    analysis_dir = Path(analysis_dir)
    state = json.loads((analysis_dir / "analysis.json").read_text(encoding="utf-8"))
    raw = json.loads((analysis_dir / "words.json").read_text(encoding="utf-8"))
    words = [Word(a, b, t) for a, b, t in raw]
    tr = Transcript(words, build_segments(words), state.get("language"), state.get("transcript_source", "none"))
    src = SourceMedia(
        video_path=Path(state["video_path"]),
        title=state["title"],
        video_id=state["video_id"],
        duration=state["duration"],
        url=state["url"],
        heatmap=state.get("heatmap") or [],
        chapters=state.get("chapters") or [],
    )
    return state, src, tr


def _snap(tr: Transcript, start: float, end: float) -> tuple[float, float]:
    """Move edges to nearby sentence boundaries so clips never cut a word in half."""
    if not tr.segments:
        return start, end
    starts = [s.start for s in tr.segments]
    ends = [s.end for s in tr.segments]
    s2 = min(starts, key=lambda x: abs(x - start))
    e2 = min(ends, key=lambda x: abs(x - end))
    if abs(s2 - start) <= 3.0:
        start = s2
    if abs(e2 - end) <= 3.0 and e2 > start:
        end = e2
    return start, end


def plan_to_clips(plan: dict, tr: Transcript, duration: float) -> list[tuple[Clip, dict]]:
    segs = tr.segments
    out: list[tuple[Clip, dict]] = []
    for k, item in enumerate(plan.get("clips", []), 1):
        if "start_segment" in item and segs:
            i = max(0, min(int(item["start_segment"]), len(segs) - 1))
            j = max(i, min(int(item.get("end_segment", i)), len(segs) - 1))
            start, end = segs[i].start, segs[j].end
        elif "start" in item and "end" in item:
            start, end = float(item["start"]), float(item["end"])
            if item.get("snap", True):
                start, end = _snap(tr, start, end)
        else:
            raise ValueError(f"clip {k}: give start/end (seconds) or start_segment/end_segment")
        start = max(0.0, start - float(item.get("pad_start", 0.15)))
        end = min(duration, end + float(item.get("pad_end", 0.35)))
        if end - start < 1.0:
            raise ValueError(f"clip {k}: too short ({end - start:.1f}s)")
        clip = Clip(
            start=start,
            end=end,
            score=float(item.get("score", 0)),
            title=str(item.get("title", f"Clip {k}")),
            hook=str(item.get("hook", "")),
            reason=str(item.get("reason", "")),
            text=tr.text_between(start, end),
        )
        overrides = {key: item[key] for key in ("reframe", "crop_x", "aspect", "captions", "hook_overlay")
                     if key in item}
        out.append((clip, overrides))
    if not out:
        raise ValueError("plan has no clips")
    return out


def render_plan(plan_path: Path, cfg: ClipConfig | None = None) -> dict:
    from .pipeline import write_srt, _timestamp_url
    from .render import render_clip, slugify

    cfg = cfg or ClipConfig()
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    analysis_dir = Path(plan.get("analysis_dir") or plan_path.parent)
    if not (analysis_dir / "analysis.json").exists():
        raise FileNotFoundError(f"No analysis.json in {analysis_dir}; set analysis_dir in the plan.")
    state, src, tr = load_prepared(analysis_dir)

    base = replace(cfg)
    for key in ("aspect", "reframe", "crop_x", "captions", "hook_overlay", "caption_highlight",
                "caption_words_per_line", "output_height"):
        if key in plan:
            setattr(base, key, plan[key])
    base.validate()

    out_dir = Path(plan.get("output_dir") or (Path(cfg.output_dir) / src.video_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    pairs = plan_to_clips(plan, tr, src.duration)
    for i, (clip, overrides) in enumerate(pairs, 1):
        ccfg = replace(base, **overrides)
        ccfg.validate()
        name = f"clip_{i:02d}_{slugify(clip.title)}"
        log(f"Rendering {i}/{len(pairs)}: {clip.title!r} [{clip.start:.1f}-{clip.end:.1f}s]")
        files = render_clip(src.video_path, clip, tr, out_dir / f"{name}.mp4", ccfg)
        words = tr.words_between(clip.start, clip.end)
        srt = out_dir / f"{name}.srt" if words else None
        if srt:
            write_srt(srt, words, clip)
        entry = clip.to_dict()
        entry.update(index=i, file=files["video"].name, path=str(files["video"].resolve()),
                     thumbnail=files["thumbnail"].name, srt=srt.name if srt else None,
                     width=files["width"], height=files["height"],
                     source_url=_timestamp_url(src, clip.start))
        results.append(entry)

    manifest = {"title": src.title, "video_id": src.video_id, "url": src.url,
                "duration": src.duration, "transcript_source": tr.source, "selector": "plan",
                "output_dir": str(out_dir.resolve()), "clips": results}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Done -> {out_dir}")
    return manifest
