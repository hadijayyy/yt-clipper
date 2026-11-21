"""ffmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH. Install FFmpeg first.")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"Command failed ({cmd[0]}):\n{tail}")
    return proc


def probe(path: Path) -> dict:
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    ).stdout
    return json.loads(out)


def probe_duration(path: Path) -> float:
    return float(probe(path)["format"].get("duration") or 0.0)


def probe_video_size(path: Path) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            w, h = int(s["width"]), int(s["height"])
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = abs(int(sd["rotation"]))
            if rot in (90, 270):
                w, h = h, w
            return w, h
    raise RuntimeError(f"No video stream in {path}")


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


_M_RE = re.compile(r"lavfi\.r128\.M=(-?[\d.]+|-inf|nan)")
_T_RE = re.compile(r"pts_time:([\d.]+)")


def loudness_curve(path: Path) -> list[float]:
    """Momentary loudness (LUFS) per second of the file. Empty if no audio."""
    if not has_audio(path):
        return []
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
            "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    per_sec: dict[int, list[float]] = {}
    t = None
    for line in proc.stdout.splitlines():
        mt = _T_RE.search(line)
        if mt:
            t = float(mt.group(1))
            continue
        mm = _M_RE.search(line)
        if mm and t is not None:
            raw = mm.group(1)
            val = -70.0 if raw in ("-inf", "nan") else max(-70.0, float(raw))
            per_sec.setdefault(int(t), []).append(val)
    if not per_sec:
        return []
    n = max(per_sec) + 1
    curve = []
    last = -70.0
    for i in range(n):
        vals = per_sec.get(i)
        if vals:
            last = sum(vals) / len(vals)
        curve.append(last)
    return curve
