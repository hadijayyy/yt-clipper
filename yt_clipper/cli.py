"""Command line: yt-clipper <youtube-url> [options]"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .config import ClipConfig


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yt-clipper serve", description="Start the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--output-dir", default="output")
    p.add_argument("--work-dir", default="work")
    return p


def build_telegram_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt-clipper telegram",
        description="Run a Telegram bot: send it a YouTube link, get clips back.",
    )
    p.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN"),
                   help="bot token from @BotFather (or env TELEGRAM_BOT_TOKEN)")
    p.add_argument("--allowed-users", default=os.environ.get("TELEGRAM_ALLOWED_USERS", ""),
                   help="comma separated Telegram user IDs allowed to use the bot "
                        "(or env TELEGRAM_ALLOWED_USERS); empty = everyone")
    p.add_argument("--cleanup", action="store_true",
                   help="delete clips and downloaded video after sending to save disk space")
    p.add_argument("-n", "--num-clips", type=int, default=3)
    p.add_argument("--min", dest="min_duration", type=float, default=20.0)
    p.add_argument("--max", dest="max_duration", type=float, default=60.0)
    p.add_argument("--selector", choices=["auto", "ai", "heuristic"], default="auto")
    p.add_argument("--aspect", choices=["vertical", "square", "original"], default="vertical")
    p.add_argument("--reframe", choices=["blur", "crop"], default="blur")
    p.add_argument("--lang", default="id,en")
    p.add_argument("--highlight", default="#FFD400")
    p.add_argument("--cookies", help="cookies.txt (Netscape format) for YouTube")
    p.add_argument("--cookies-from-browser", help="e.g. chrome, firefox, edge")
    p.add_argument("-o", "--output-dir", default="output")
    p.add_argument("--work-dir", default="work")
    return p


def run_telegram(argv: list[str]) -> int:
    a = build_telegram_parser().parse_args(argv)
    if not a.token:
        print("error: missing bot token. Use --token or set TELEGRAM_BOT_TOKEN "
              "(create a bot with @BotFather on Telegram).", file=sys.stderr)
        return 1
    try:
        allowed = {int(x) for x in re.split(r"[,\s]+", a.allowed_users) if x.strip()}
    except ValueError:
        print("error: --allowed-users must be numeric Telegram user IDs", file=sys.stderr)
        return 1
    cfg = ClipConfig(
        num_clips=a.num_clips,
        min_duration=a.min_duration,
        max_duration=a.max_duration,
        selector=a.selector,
        aspect=a.aspect,
        reframe=a.reframe,
        languages=[x.strip() for x in a.lang.split(",") if x.strip()],
        caption_highlight=a.highlight,
        cookies=a.cookies,
        cookies_from_browser=a.cookies_from_browser,
        output_dir=Path(a.output_dir),
        work_dir=Path(a.work_dir),
    )
    from .media import require_ffmpeg
    from .telegram_bot import run_bot

    try:
        require_ffmpeg()
        run_bot(a.token, cfg, allowed, cleanup=a.cleanup)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _common_download_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cookies", help="cookies.txt (Netscape format) for YouTube")
    p.add_argument("--cookies-from-browser", help="e.g. chrome, firefox, edge")
    p.add_argument("--work-dir", default="work")


def run_prepare(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="yt-clipper prepare",
        description="Download + analyze a video so an agent can pick clips: writes "
                    "transcript.txt, overview.md and frame contact sheets.",
    )
    p.add_argument("url", nargs="?", help="YouTube URL")
    p.add_argument("--video", help="local video file instead of a URL")
    p.add_argument("--subs", help="subtitle file (.vtt) for --video")
    p.add_argument("--transcript", choices=["auto", "youtube", "whisper", "none"], default="auto")
    p.add_argument("--lang", default="id,en")
    p.add_argument("--whisper-model", default="small")
    p.add_argument("--min", dest="min_duration", type=float, default=20.0,
                   help="min clip length used for the heuristic shortlist")
    p.add_argument("--max", dest="max_duration", type=float, default=60.0)
    p.add_argument("--no-sheets", action="store_true", help="skip frame contact sheets")
    _common_download_args(p)
    a = p.parse_args(argv)
    if not a.url and not a.video:
        p.error("give a YouTube URL or --video FILE")
    from .analyze import prepare
    from .download import local_source

    cfg = ClipConfig(transcript=a.transcript, whisper_model=a.whisper_model,
                     languages=[x.strip() for x in a.lang.split(",") if x.strip()],
                     min_duration=a.min_duration, max_duration=a.max_duration,
                     cookies=a.cookies, cookies_from_browser=a.cookies_from_browser,
                     work_dir=Path(a.work_dir))
    try:
        cfg.validate()
        src = local_source(Path(a.video), Path(a.subs) if a.subs else None) if a.video else a.url
        out = prepare(src, cfg, sheets=not a.no_sheets)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    state = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    print(f"ANALYSIS_DIR={out.resolve()}")
    print(f"title: {state['title']}")
    print(f"duration: {state['duration']:.0f}s  transcript: {state['transcript_source']}")
    print(f"read: {out.resolve() / 'overview.md'}")
    print(f"read: {out.resolve() / 'transcript.txt'}")
    for sheet in state["sheets"]:
        print(f"view: {out.resolve() / sheet}")
    return 0


def run_frames(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="yt-clipper frames",
                                description="Contact sheet of a time range of a prepared video.")
    p.add_argument("analysis_dir")
    p.add_argument("--start", type=float, required=True, help="seconds")
    p.add_argument("--end", type=float, required=True, help="seconds")
    p.add_argument("--count", type=int, default=12)
    a = p.parse_args(argv)
    from .analyze import range_sheet

    try:
        print(range_sheet(Path(a.analysis_dir), a.start, a.end, a.count).resolve())
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def run_render_plan(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="yt-clipper render-plan",
                                description="Render the clips listed in a plan JSON file.")
    p.add_argument("plan")
    p.add_argument("-o", "--output-dir", default="output")
    p.add_argument("--height", dest="output_height", type=int, default=1920)
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    from .analyze import render_plan

    cfg = ClipConfig(output_dir=Path(a.output_dir), output_height=a.output_height,
                     crf=a.crf, preset=a.preset)
    try:
        m = render_plan(Path(a.plan), cfg)
    except (RuntimeError, ValueError, FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(m, indent=2, ensure_ascii=False))
    else:
        print(f"OUTPUT_DIR={m['output_dir']}")
        for c in m["clips"]:
            print(f"{c['index']}. [{c['start']:.1f}-{c['end']:.1f}s, {c['duration']:.1f}s] {c['title']}")
            print(f"   {c['path']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt-clipper",
        description="Turn a YouTube link into short vertical clips with animated captions. "
        "Run `yt-clipper serve` for the web UI or `yt-clipper telegram` for the Telegram bot.",
    )
    p.add_argument("url", nargs="?", help="YouTube URL (or use --video for a local file)")
    p.add_argument("--video", help="Local video file instead of a YouTube URL")
    p.add_argument("--subs", help="Subtitle file (.vtt) for --video")

    g = p.add_argument_group("clips")
    g.add_argument("-n", "--num-clips", type=int, default=5)
    g.add_argument("--min", dest="min_duration", type=float, default=20.0, help="min seconds")
    g.add_argument("--max", dest="max_duration", type=float, default=60.0, help="max seconds")
    g.add_argument("--selector", choices=["auto", "ai", "heuristic"], default="auto",
                   help="auto = Claude if credentials exist, else heuristic")
    g.add_argument("--ai-model", default="claude-opus-5-5")
    g.add_argument("--ai-effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    g.add_argument("--prompt", dest="ai_instructions", default="",
                   help='Extra guidance for the AI, e.g. "fokus ke tips bisnis"')

    g = p.add_argument_group("layout")
    g.add_argument("--aspect", choices=["vertical", "square", "original"], default="vertical")
    g.add_argument("--reframe", choices=["blur", "crop"], default="blur")
    g.add_argument("--crop-x", type=float, default=0.5, help="crop center 0..1 (reframe=crop)")
    g.add_argument("--height", dest="output_height", type=int, default=1920)

    g = p.add_argument_group("captions")
    g.add_argument("--no-captions", action="store_true")
    g.add_argument("--no-hook", action="store_true", help="hide hook text at the top")
    g.add_argument("--words-per-line", type=int, default=3)
    g.add_argument("--font", default="DejaVu Sans")
    g.add_argument("--font-size", type=int, default=0)
    g.add_argument("--highlight", default="#FFD400", help="active word color")

    g = p.add_argument_group("transcript")
    g.add_argument("--transcript", choices=["auto", "youtube", "whisper", "none"], default="auto")
    g.add_argument("--lang", default="id,en", help="preferred subtitle languages, comma separated")
    g.add_argument("--whisper-model", default="small")

    g = p.add_argument_group("download")
    g.add_argument("--max-height", type=int, default=1080)
    g.add_argument("--cookies", help="cookies.txt (Netscape format) for YouTube")
    g.add_argument("--cookies-from-browser", help="e.g. chrome, firefox, edge")

    g = p.add_argument_group("output")
    g.add_argument("-o", "--output-dir", default="output")
    g.add_argument("--work-dir", default="work")
    g.add_argument("--crf", type=int, default=20)
    g.add_argument("--preset", default="veryfast")
    g.add_argument("--no-loudnorm", action="store_true")
    g.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    return p


def config_from_args(a: argparse.Namespace) -> ClipConfig:
    return ClipConfig(
        num_clips=a.num_clips,
        min_duration=a.min_duration,
        max_duration=a.max_duration,
        aspect=a.aspect,
        reframe=a.reframe,
        crop_x=a.crop_x,
        output_height=a.output_height,
        captions=not a.no_captions,
        hook_overlay=not a.no_hook,
        caption_words_per_line=a.words_per_line,
        caption_font=a.font,
        caption_font_size=a.font_size,
        caption_highlight=a.highlight,
        transcript=a.transcript,
        languages=[x.strip() for x in a.lang.split(",") if x.strip()],
        whisper_model=a.whisper_model,
        selector=a.selector,
        ai_model=a.ai_model,
        ai_effort=a.ai_effort,
        ai_instructions=a.ai_instructions,
        max_height=a.max_height,
        cookies=a.cookies,
        cookies_from_browser=a.cookies_from_browser,
        crf=a.crf,
        preset=a.preset,
        loudnorm=not a.no_loudnorm,
        work_dir=Path(a.work_dir),
        output_dir=Path(a.output_dir),
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "telegram":
        return run_telegram(argv[1:])
    sub = {"prepare": run_prepare, "frames": run_frames, "render-plan": run_render_plan}
    if argv and argv[0] in sub:
        return sub[argv[0]](argv[1:])
    if argv and argv[0] == "serve":
        a = build_serve_parser().parse_args(argv[1:])
        from .web import serve

        serve(a.host, a.port, Path(a.output_dir), Path(a.work_dir))
        return 0

    parser = build_parser()
    a = parser.parse_args(argv)
    if not a.url and not a.video:
        parser.error("give a YouTube URL or --video FILE")

    from .pipeline import run_pipeline

    cfg = config_from_args(a)
    try:
        if a.video:
            from .download import local_source

            source = local_source(Path(a.video), Path(a.subs) if a.subs else None)
        else:
            source = a.url
        manifest = run_pipeline(source, cfg)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print(f"\n{len(manifest['clips'])} clips -> {manifest['output_dir']}")
        for c in manifest["clips"]:
            print(f"  {c['index']:>2}. [{c['start']:7.1f}s +{c['duration']:5.1f}s] "
                  f"score {c['score']:5.1f}  {c['title']}")
            print(f"      {c['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
