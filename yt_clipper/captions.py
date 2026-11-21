"""Generate animated word-by-word captions as an ASS subtitle file."""

from __future__ import annotations

import re
from pathlib import Path

from .config import ClipConfig
from .transcript import Word

_SENT_END = re.compile(r"[.!?…,;:]['\")\]]*$")


def hex_to_ass(color: str, alpha: int = 0) -> str:
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"Bad color {color!r}, expected #RRGGBB")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def chunk_words(words: list[Word], per_line: int, max_gap: float = 0.6) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= per_line or w.start - cur[-1].end > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if _SENT_END.search(w.text):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    width: int,
    height: int,
    cfg: ClipConfig,
    hook: str = "",
) -> str:
    vertical = height > width
    font_size = cfg.caption_font_size or int(height * (0.043 if vertical else 0.06))
    margin_v = int(height * (0.24 if vertical else 0.08))
    hook_size = int(font_size * 0.72)
    hi = "&H" + hex_to_ass(cfg.caption_highlight)[4:] + "&"  # override tag form: &HBBGGRR&
    outline = max(2, font_size // 14)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{cfg.caption_font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},2,{int(width * 0.08)},{int(width * 0.08)},{margin_v},1
Style: Hook,{cfg.caption_font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,{max(6, hook_size // 4)},0,8,{int(width * 0.08)},{int(width * 0.08)},{int(height * 0.1)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    dur = clip_end - clip_start

    if hook and cfg.hook_overlay:
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(3.5, dur))},Hook,,0,0,0,,"
            f"{{\\fad(150,250)}}{_esc(hook)}"
        )

    if cfg.captions and words:
        rel = [
            Word(max(0.0, w.start - clip_start), min(dur, w.end - clip_start), w.text)
            for w in words
            if w.end > clip_start and w.start < clip_end
        ]
        for chunk in chunk_words(rel, max(1, cfg.caption_words_per_line)):
            chunk_end = chunk[-1].end
            for k, w in enumerate(chunk):
                start = w.start
                end = chunk[k + 1].start if k + 1 < len(chunk) else chunk_end
                if end - start < 0.05:
                    end = start + 0.05
                parts = []
                for m, other in enumerate(chunk):
                    t = _esc(other.text.upper())
                    if m == k:
                        parts.append(f"{{\\c{hi}\\fscx108\\fscy108}}{t}{{\\c&HFFFFFF&\\fscx100\\fscy100}}")
                    else:
                        parts.append(t)
                pop = "{\\fscx92\\fscy92\\t(0,80,\\fscx100\\fscy100)}" if k == 0 else ""
                events.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{pop}{' '.join(parts)}"
                )

    return header + "\n".join(events) + "\n"


def write_ass(path: Path, *args, **kwargs) -> Path:
    path = Path(path)
    path.write_text(build_ass(*args, **kwargs), encoding="utf-8")
    return path
