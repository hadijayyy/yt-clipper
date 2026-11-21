"""Cut, reframe and caption a clip with ffmpeg."""

from __future__ import annotations

import re
from pathlib import Path

from .captions import write_ass
from .config import ClipConfig
from .highlights import Clip
from .media import has_audio, probe_video_size, run
from .transcript import Transcript


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def output_size(src_w: int, src_h: int, cfg: ClipConfig) -> tuple[int, int]:
    if cfg.aspect == "vertical":
        h = cfg.output_height
        return _even(h * 9 / 16), _even(h)
    if cfg.aspect == "square":
        side = _even(cfg.output_height * 9 / 16)
        return side, side
    # original: keep the source aspect, cap height
    h = min(src_h, cfg.output_height)
    return _even(src_w * h / src_h), _even(h)


def _filter_path(p: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    s = str(p.resolve()).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")


def build_filtergraph(src_w: int, src_h: int, W: int, H: int, cfg: ClipConfig, ass: Path | None) -> str:
    same_aspect = abs(src_w / src_h - W / H) < 0.01
    if cfg.aspect == "original" or same_aspect:
        chain = f"[0:v]scale={W}:{H}:flags=lanczos,setsar=1[v0]"
    elif cfg.reframe == "crop":
        cx = f"{cfg.crop_x:.4f}"
        chain = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H}:(in_w-{W})*{cx}:(in_h-{H})/2,setsar=1[v0]"
        )
    else:  # blur: fit the full frame over a blurred, zoomed copy of itself
        bw, bh = _even(W / 4), _even(H / 4)
        chain = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=10:2,eq=brightness=-0.12:saturation=1.2,scale={W}:{H}[bg];"
            f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
        )
    if ass is not None:
        chain += f";[v0]ass=filename='{_filter_path(ass)}'[vout]"
    else:
        chain += ";[v0]null[vout]"
    return chain


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:max_len].strip("-") or "clip"


def render_clip(
    src_video: Path,
    clip: Clip,
    transcript: Transcript,
    out_path: Path,
    cfg: ClipConfig,
) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src_w, src_h = probe_video_size(src_video)
    W, H = output_size(src_w, src_h, cfg)

    ass_path = None
    if cfg.captions or (cfg.hook_overlay and clip.hook):
        ass_path = out_path.with_suffix(".ass")
        write_ass(
            ass_path,
            transcript.words_between(clip.start, clip.end),
            clip.start,
            clip.end,
            W,
            H,
            cfg,
            hook=clip.hook,
        )

    graph = build_filtergraph(src_w, src_h, W, H, cfg, ass_path)
    audio = has_audio(src_video)
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-loglevel", "error",
        "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}",
        "-i", str(src_video),
        "-filter_complex", graph,
        "-map", "[vout]",
    ]
    if audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
        afilters = ["afade=t=in:d=0.05", f"afade=t=out:st={max(0, clip.duration - 0.15):.3f}:d=0.15"]
        if cfg.loudnorm:
            afilters.insert(0, "loudnorm=I=-14:TP=-1.5:LRA=11")
        cmd += ["-af", ",".join(afilters)]
    cmd += [
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart",
        str(out_path),
    ]
    run(cmd)

    thumb = out_path.with_suffix(".jpg")
    run([
        "ffmpeg", "-hide_banner", "-y", "-loglevel", "error",
        "-ss", f"{min(1.0, clip.duration / 2):.3f}", "-i", str(out_path),
        "-frames:v", "1", "-q:v", "3", str(thumb),
    ])
    return {"video": out_path, "thumbnail": thumb, "subtitles": ass_path, "width": W, "height": H}
