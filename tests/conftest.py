import subprocess
from pathlib import Path

import pytest


def _ts(x: float) -> str:
    h = int(x // 3600)
    m = int(x % 3600 // 60)
    return f"{h:02d}:{m:02d}:{x % 60:06.3f}"


def write_auto_vtt(path: Path, phrases: list[tuple[float, list[str]]], step: float = 0.4) -> Path:
    """Write a YouTube auto-caption style VTT (rolling lines + inline word timestamps)."""
    out = ["WEBVTT", "Kind: captions", "Language: id", ""]
    prev = " "
    for t, words in phrases:
        end = t + step * len(words)
        line = words[0] + "".join(
            f"<{_ts(t + step * i)}><c> {w}</c>" for i, w in enumerate(words) if i > 0
        )
        out += [f"{_ts(t)} --> {_ts(end)} align:start position:0%", prev, line, ""]
        out += [f"{_ts(end)} --> {_ts(end + 0.01)} align:start position:0%", " ".join(words), " ", ""]
        prev = " ".join(words)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def sample_media(tmp_path_factory):
    """40 s 640x360 test video whose audio is loud between 20 s and 32 s, plus subtitles."""
    d = tmp_path_factory.mktemp("media")
    video = d / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=40",
            "-f", "lavfi", "-i", "sine=f=440:d=40",
            "-af", "volume='if(between(t,20,32),1.0,0.1)':eval=frame",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video),
        ],
        check=True,
    )
    phrases = []
    t = 0.5
    i = 0
    while t < 38:
        if 20 <= t < 32:
            words = ["ternyata", "rahasia", "terbesar", "adalah", "ini?"]
        else:
            words = ["kita", "lanjut", "bahas", "topik", f"nomor{i}."]
        phrases.append((t, words))
        t += 0.4 * len(words) + 0.3
        i += 1
    subs = write_auto_vtt(d / "sample.id.vtt", phrases)
    return video, subs
