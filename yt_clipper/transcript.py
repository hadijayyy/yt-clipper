"""Transcript model + loaders (YouTube WebVTT incl. word-timed auto captions, Whisper)."""

from __future__ import annotations

import bisect
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .log import log


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    words: list[Word]
    segments: list[Segment]
    language: str | None = None
    source: str = "none"

    def __post_init__(self) -> None:
        self.words.sort(key=lambda w: w.start)
        self._starts = [w.start for w in self.words]

    @property
    def empty(self) -> bool:
        return not self.words

    def words_between(self, start: float, end: float) -> list[Word]:
        # Words are short, so anything overlapping [start, end) starts after start - 5s.
        lo = bisect.bisect_left(self._starts, start - 5.0)
        hi = bisect.bisect_left(self._starts, end)
        return [w for w in self.words[lo:hi] if w.end > start]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words_between(start, end))


# --------------------------------------------------------------------------- VTT

_TS = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
_CUE_RE = re.compile(rf"^{_TS}\s+-->\s+{_TS}")
_INLINE_TS_RE = re.compile(r"<(\d+:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})>")
_TAG_RE = re.compile(r"</?[^>]+>")
_NOISE_RE = re.compile(r"\[(?:music|musik|applause|tepuk tangan|laughter|tertawa)[^\]]*\]", re.I)


def _ts_to_sec(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_ts(ts: str) -> float:
    parts = re.split(r"[:.,]", ts)
    if len(parts) == 3:
        parts = ["0"] + parts
    return _ts_to_sec(*parts)


def _clean(text: str) -> str:
    text = html.unescape(_TAG_RE.sub("", text))
    text = _NOISE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _read_cues(path: Path) -> list[tuple[float, float, list[str]]]:
    cues = []
    lines = [ln.rstrip("\r\n") for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    i = 0
    while i < len(lines):
        m = _CUE_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        g = m.groups()
        start, end = _ts_to_sec(*g[:4]), _ts_to_sec(*g[4:])
        i += 1
        body = []
        # YouTube puts whitespace-only lines *inside* cues, so only a truly empty
        # line (or the next timing line) ends the cue.
        while i < len(lines) and lines[i] != "" and not _CUE_RE.match(lines[i].strip()):
            body.append(lines[i])
            i += 1
        cues.append((start, end, body))
    return cues


def parse_vtt(path: Path) -> list[Word]:
    cues = _read_cues(path)
    word_timed = any(_INLINE_TS_RE.search(line) for _, _, body in cues for line in body)
    words: list[Word] = []

    if word_timed:
        # YouTube auto-captions: the line carrying <timestamps> holds the new words.
        for start, end, body in cues:
            for line in body:
                if not _INLINE_TS_RE.search(line):
                    continue
                pieces = _INLINE_TS_RE.split(line)
                # pieces = [text0, ts1, text1, ts2, text2, ...]
                starts = [start] + [_parse_ts(t) for t in pieces[1::2]]
                texts = pieces[0::2]
                for k, raw in enumerate(texts):
                    t = _clean(raw)
                    if not t:
                        continue
                    w_start = starts[k]
                    w_end = starts[k + 1] if k + 1 < len(starts) else end
                    for tok in t.split():
                        words.append(Word(w_start, max(w_end, w_start + 0.05), tok))
        words.sort(key=lambda w: w.start)
        return words

    # Plain subtitles: spread words evenly across each cue, drop rolling duplicates.
    prev_text = ""
    for start, end, body in cues:
        text = _clean(" ".join(body))
        if not text or text == prev_text:
            continue
        if prev_text and text.startswith(prev_text):
            text = text[len(prev_text):].strip()
        prev_text = _clean(" ".join(body))
        toks = text.split()
        if not toks:
            continue
        step = max(end - start, 0.1) / len(toks)
        for k, tok in enumerate(toks):
            words.append(Word(start + k * step, start + (k + 1) * step, tok))
    return words


# --------------------------------------------------------------------- segments

_SENT_END = re.compile(r"[.!?…。！？]['\")\]]*$")


def build_segments(words: list[Word], max_len: float = 14.0, gap: float = 0.9) -> list[Segment]:
    segs: list[Segment] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            pause = w.start - cur[-1].end
            too_long = w.end - cur[0].start > max_len
            if pause > gap or too_long:
                segs.append(_mk_seg(cur))
                cur = []
        cur.append(w)
        if _SENT_END.search(w.text) and cur[-1].end - cur[0].start > 1.5:
            segs.append(_mk_seg(cur))
            cur = []
    if cur:
        segs.append(_mk_seg(cur))
    return segs


def _mk_seg(ws: list[Word]) -> Segment:
    return Segment(ws[0].start, ws[-1].end, " ".join(w.text for w in ws), list(ws))


def from_vtt(path: Path, language: str | None = None) -> Transcript:
    words = parse_vtt(Path(path))
    return Transcript(words, build_segments(words), language, source="youtube")


# ---------------------------------------------------------------------- whisper

def from_whisper(media: Path, model_size: str = "small", language: str | None = None) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "No subtitles available and faster-whisper is not installed. "
            "Run: pip install faster-whisper"
        ) from e
    log(f"Transcribing with Whisper ({model_size}) - this can take a while ...")
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, info = model.transcribe(
        str(media), word_timestamps=True, vad_filter=True, language=language
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append(Word(float(w.start), float(w.end), t))
    return Transcript(words, build_segments(words), info.language, source="whisper")


def load_transcript(src, cfg) -> Transcript:
    """Pick the best transcript for a SourceMedia according to cfg.transcript."""
    mode = cfg.transcript
    if mode == "none":
        return Transcript([], [], source="none")
    if mode in ("auto", "youtube") and src.subtitle_path and Path(src.subtitle_path).exists():
        tr = from_vtt(src.subtitle_path, src.subtitle_lang)
        if not tr.empty:
            log(f"Using subtitles ({src.subtitle_lang or 'unknown'}): {len(tr.words)} words")
            return tr
    if mode == "youtube":
        log("No usable YouTube subtitles; continuing without transcript.")
        return Transcript([], [], source="none")
    try:
        lang = cfg.languages[0] if cfg.languages and mode == "whisper" else None
        return from_whisper(src.video_path, cfg.whisper_model, lang)
    except RuntimeError as e:
        if mode == "whisper":
            raise
        log(f"{e} -> continuing without transcript (no captions, heuristic only).")
        return Transcript([], [], source="none")
