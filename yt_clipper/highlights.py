"""Find the best moments: heuristic scoring (signals) and Claude-based selection."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict, field

from .config import ClipConfig
from .log import log
from .transcript import Segment, Transcript


@dataclass
class Clip:
    start: float
    end: float
    score: float = 0.0
    title: str = ""
    hook: str = ""
    reason: str = ""
    text: str = ""
    signals: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 2)
        return d


# ----------------------------------------------------------------- signals

HOOK_WORDS = {
    # English
    "secret", "never", "always", "best", "worst", "why", "how", "mistake", "truth",
    "crazy", "insane", "important", "actually", "wrong", "money", "million", "free",
    "stop", "nobody", "everyone", "biggest", "first", "warning", "shocking", "wow",
    # Bahasa Indonesia
    "rahasia", "jangan", "selalu", "terbaik", "terburuk", "kenapa", "mengapa", "cara",
    "salah", "penting", "sebenarnya", "gila", "uang", "juta", "miliar", "gratis",
    "berhenti", "semua", "pertama", "bahaya", "ternyata", "parah", "banget", "wajib",
}
_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


class Signals:
    """Per-second signal arrays over the whole video."""

    def __init__(self, duration: float, heatmap: list[dict], loudness: list[float]):
        n = max(1, int(math.ceil(duration)))
        self.n = n
        self.heat = [0.0] * n
        self.has_heat = bool(heatmap)
        for h in heatmap:
            a = int(max(0, h.get("start_time", 0)))
            b = int(min(n, math.ceil(h.get("end_time", 0))))
            for s in range(a, b):
                self.heat[s] = max(self.heat[s], float(h.get("value", 0)))

        self.has_loud = bool(loudness)
        loud = (loudness + [loudness[-1]] * n)[:n] if loudness else [0.0] * n
        # Ignore near-silence when computing the reference level.
        voiced = sorted(v for v in loud if v > -50) or [0.0]
        med = voiced[len(voiced) // 2]
        spread = (voiced[int(len(voiced) * 0.9)] - voiced[int(len(voiced) * 0.1)]) or 1.0
        self.loud = [(v - med) / spread for v in loud]

    def mean(self, arr: list[float], start: float, end: float) -> float:
        a, b = max(0, int(start)), min(self.n, max(int(start) + 1, int(math.ceil(end))))
        seg = arr[a:b]
        return sum(seg) / len(seg) if seg else 0.0

    def peak(self, arr: list[float], start: float, end: float) -> float:
        a, b = max(0, int(start)), min(self.n, max(int(start) + 1, int(math.ceil(end))))
        return max(arr[a:b], default=0.0)


def _hook_score(text: str) -> float:
    toks = [t.lower() for t in _WORD_RE.findall(text)]
    if not toks:
        return 0.0
    hits = sum(1 for t in toks if t in HOOK_WORDS)
    hits += text.count("?") * 1.5 + text.count("!")
    hits += sum(1 for t in toks if t.isdigit()) * 0.5
    return hits / math.sqrt(len(toks))


def _opening_hook(tr: Transcript, start: float) -> float:
    """Extra weight on the first ~5 seconds: a clip lives or dies on its opening."""
    return _hook_score(tr.text_between(start, start + 5.0))


# ------------------------------------------------------------- candidates

def _candidate_windows(tr: Transcript, duration: float, cfg: ClipConfig) -> list[tuple[float, float]]:
    lo, hi = cfg.min_duration, cfg.max_duration
    wins: list[tuple[float, float]] = []
    segs = tr.segments
    if segs:
        for i, s in enumerate(segs):
            for j in range(i, len(segs)):
                d = segs[j].end - s.start
                if d > hi:
                    break
                if d >= lo:
                    wins.append((s.start, segs[j].end))
    if not wins:
        # No transcript (or segments too sparse): fixed sliding windows.
        lengths = sorted({lo, (lo + hi) / 2, hi})
        stride = max(2.0, lo / 4)
        t = 0.0
        while t + lo <= duration:
            for L in lengths:
                if t + L <= duration:
                    wins.append((t, t + L))
            t += stride
        if not wins and duration > 0:
            wins.append((0.0, min(duration, hi)))
    return wins


def _normalize(vals: list[float]) -> list[float]:
    if not vals:
        return vals
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def select_non_overlapping(cands: list[Clip], n: int, min_gap: float = 1.0) -> list[Clip]:
    chosen: list[Clip] = []
    for c in sorted(cands, key=lambda c: c.score, reverse=True):
        if all(c.end + min_gap <= o.start or c.start >= o.end + min_gap for o in chosen):
            chosen.append(c)
            if len(chosen) >= n:
                break
    return chosen


def heuristic_select(tr: Transcript, sig: Signals, duration: float, cfg: ClipConfig) -> list[Clip]:
    wins = _candidate_windows(tr, duration, cfg)
    log(f"Scoring {len(wins)} candidate windows (heuristic) ...")
    target = (cfg.min_duration + cfg.max_duration) / 2

    feats = {"heat": [], "heat_peak": [], "loud": [], "density": [], "hook": [], "opening": []}
    for a, b in wins:
        feats["heat"].append(sig.mean(sig.heat, a, b))
        feats["heat_peak"].append(sig.peak(sig.heat, a, b))
        feats["loud"].append(sig.mean(sig.loud, a, b))
        n_words = len(tr.words_between(a, b)) if not tr.empty else 0
        feats["density"].append(n_words / max(b - a, 1e-6))
        feats["hook"].append(_hook_score(tr.text_between(a, b)) if not tr.empty else 0.0)
        feats["opening"].append(_opening_hook(tr, a) if not tr.empty else 0.0)

    norm = {k: _normalize(v) for k, v in feats.items()}
    weights = {
        "heat": 0.30 if sig.has_heat else 0.0,
        "heat_peak": 0.15 if sig.has_heat else 0.0,
        "loud": 0.20 if sig.has_loud else 0.0,
        "density": 0.10 if not tr.empty else 0.0,
        "hook": 0.12 if not tr.empty else 0.0,
        "opening": 0.13 if not tr.empty else 0.0,
    }
    total_w = sum(weights.values()) or 1.0

    cands = []
    for idx, (a, b) in enumerate(wins):
        s = sum(weights[k] * norm[k][idx] for k in weights) / total_w
        # Mild preference for durations near the middle of the allowed range.
        s *= 1.0 - 0.15 * abs((b - a) - target) / max(target, 1.0)
        cands.append(
            Clip(
                start=a,
                end=b,
                score=round(100 * s, 1),
                signals={k: round(feats[k][idx], 3) for k in feats},
            )
        )
    chosen = select_non_overlapping(cands, cfg.num_clips)
    for i, c in enumerate(chosen, 1):
        c.text = tr.text_between(c.start, c.end)
        c.title = _auto_title(c.text) or f"Clip {i}"
        c.hook = _auto_title(c.text, max_words=7)
        c.reason = "heuristic: " + ", ".join(
            f"{k}={v}" for k, v in c.signals.items() if weights.get(k)
        )
    return chosen


def _auto_title(text: str, max_words: int = 8) -> str:
    words = text.split()
    if not words:
        return ""
    t = " ".join(words[:max_words])
    return t[0].upper() + t[1:] + ("..." if len(words) > max_words else "")


# --------------------------------------------------------------- AI select

_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_segment": {"type": "integer"},
                    "end_segment": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["start_segment", "end_segment", "title", "hook", "reason", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

_SYSTEM = """You are a senior short-form video editor. You pick the moments of a long video \
that will work best as standalone vertical clips (TikTok, Reels, YouTube Shorts).

A good clip:
- opens with a strong hook in the first 3 seconds (a bold claim, question, surprise, conflict, punchline setup);
- is self-contained: a viewer with no context understands it;
- has a payoff (answer, punchline, insight, reveal) before it ends, and ends on a complete thought;
- favours emotion, humour, controversy, concrete advice, stories and quotable lines over filler, intros, sponsor reads and outros.

You receive the transcript as numbered segments with timestamps in seconds. Choose each clip as an \
inclusive range of segment numbers. Clips must not overlap. Respect the duration limits exactly. \
Write title and hook in the same language as the transcript. Titles are short and catchy (max ~8 words). \
The hook is the on-screen text for the first seconds. score is 0-100 for predicted virality. \
Order clips from best to worst."""


def _fmt_t(s: float) -> str:
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def _build_prompt(tr: Transcript, sig: Signals, meta: dict, cfg: ClipConfig) -> str:
    lines = [f"[{i}] {seg.start:.2f}-{seg.end:.2f} ({_fmt_t(seg.start)}) {seg.text}"
             for i, seg in enumerate(tr.segments)]
    parts = [
        f"Video title: {meta.get('title', '')}",
        f"Duration: {meta.get('duration', 0):.0f} s",
    ]
    if meta.get("description"):
        parts.append(f"Description (truncated):\n{meta['description'][:1200]}")
    if meta.get("chapters"):
        ch = "\n".join(f"- {c.get('start_time', 0):.0f}s {c.get('title', '')}" for c in meta["chapters"])
        parts.append(f"Chapters:\n{ch}")
    if sig.has_heat:
        top = sorted(range(sig.n), key=lambda s: sig.heat[s], reverse=True)
        peaks, seen = [], []
        for s in top:
            if all(abs(s - p) > 20 for p in seen):
                seen.append(s)
                peaks.append(f"{s}s (replay intensity {sig.heat[s]:.2f})")
            if len(peaks) >= 10:
                break
        parts.append("YouTube 'most replayed' peaks (strong signal of interesting moments):\n" + ", ".join(peaks))
    parts.append(
        f"Pick exactly {cfg.num_clips} clips (fewer only if the video is too short). "
        f"Each clip must be between {cfg.min_duration:.0f} and {cfg.max_duration:.0f} seconds long "
        f"(end time of end_segment minus start time of start_segment)."
    )
    if cfg.ai_instructions:
        parts.append(f"Extra instructions from the user: {cfg.ai_instructions}")
    parts.append("Transcript segments:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _fit_range(segs: list[Segment], i: int, j: int, cfg: ClipConfig) -> tuple[int, int]:
    """Grow/shrink a segment range so its duration lands inside [min, max]."""
    i, j = max(0, min(i, len(segs) - 1)), max(0, min(j, len(segs) - 1))
    if j < i:
        i, j = j, i
    dur = lambda: segs[j].end - segs[i].start  # noqa: E731
    while dur() > cfg.max_duration and j > i:
        j -= 1
    while dur() < cfg.min_duration:
        if j + 1 < len(segs) and segs[j + 1].end - segs[i].start <= cfg.max_duration:
            j += 1
        elif i > 0 and segs[j].end - segs[i - 1].start <= cfg.max_duration:
            i -= 1
        else:
            break
    return i, j


def ai_select(tr: Transcript, sig: Signals, meta: dict, cfg: ClipConfig) -> list[Clip]:
    import anthropic

    client = anthropic.Anthropic()
    prompt = _build_prompt(tr, sig, meta, cfg)
    log(f"Asking Claude ({cfg.ai_model}) to pick {cfg.num_clips} highlights "
        f"from {len(tr.segments)} segments ...")
    response = client.beta.messages.create(
        model=cfg.ai_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={
            "effort": cfg.ai_effort,
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this transcript.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude response was cut off (max_tokens).")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)

    segs = tr.segments
    clips: list[Clip] = []
    for item in data.get("clips", []):
        i, j = _fit_range(segs, int(item["start_segment"]), int(item["end_segment"]), cfg)
        start, end = segs[i].start, segs[j].end
        clips.append(
            Clip(
                start=start,
                end=end,
                score=float(item.get("score", 0)),
                title=item.get("title", "").strip(),
                hook=item.get("hook", "").strip(),
                reason=item.get("reason", "").strip(),
                text=tr.text_between(start, end),
                signals={"heat": round(sig.mean(sig.heat, start, end), 3),
                         "loud": round(sig.mean(sig.loud, start, end), 3)},
            )
        )
    # Keep Claude's order but drop any overlaps it produced after range fitting.
    chosen: list[Clip] = []
    for c in clips:
        if all(c.end <= o.start or c.start >= o.end for o in chosen):
            chosen.append(c)
    return chosen[: cfg.num_clips]


def _describe_ai_error(e: Exception) -> str:
    try:
        import anthropic
    except ImportError:
        return str(e)
    if isinstance(e, TypeError) and "authentication" in str(e).lower():
        return "no Claude credentials; set ANTHROPIC_API_KEY"
    if isinstance(e, anthropic.AuthenticationError):
        return "invalid ANTHROPIC_API_KEY"
    if isinstance(e, anthropic.NotFoundError):
        return "model not found; check --ai-model"
    if isinstance(e, anthropic.RateLimitError):
        return "rate limited by the Claude API; try again later"
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude API error {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "cannot reach the Claude API (network)"
    return f"{type(e).__name__}: {e}"


def find_highlights(tr: Transcript, sig: Signals, meta: dict, cfg: ClipConfig) -> list[Clip]:
    mode = cfg.selector
    if mode in ("auto", "ai") and not tr.segments:
        if mode == "ai":
            raise RuntimeError("AI selection needs a transcript (subtitles or Whisper).")
        log("No transcript - using heuristic selection.")
        mode = "heuristic"

    if mode in ("auto", "ai"):
        try:
            clips = ai_select(tr, sig, meta, cfg)
            if clips:
                return clips
            log("Claude returned no clips; falling back to heuristic.")
        except ImportError:
            if mode == "ai":
                raise RuntimeError("Install the Anthropic SDK: pip install anthropic")
            log("anthropic SDK not installed - using heuristic selection.")
        except Exception as e:  # auth/network/API errors -> heuristic in auto mode
            reason = _describe_ai_error(e)
            if mode == "ai":
                raise RuntimeError(f"AI selection failed: {reason}") from e
            log(f"AI selection unavailable ({reason}) - using heuristic.")
    return heuristic_select(tr, sig, meta.get("duration", 0.0), cfg)
