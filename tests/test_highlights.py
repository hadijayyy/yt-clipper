from yt_clipper.config import ClipConfig
from yt_clipper.highlights import Clip, Signals, _fit_range, heuristic_select, select_non_overlapping
from yt_clipper.transcript import Segment, Transcript, Word, build_segments


def make_transcript(n_seconds=120):
    words = []
    for s in range(0, n_seconds):
        txt = "rahasia" if 60 <= s < 80 else "biasa"
        words.append(Word(s + 0.1, s + 0.6, txt + ("." if s % 4 == 3 else "")))
    return Transcript(words, build_segments(words))


def test_non_overlapping_selection():
    cands = [Clip(0, 30, 90), Clip(10, 40, 80), Clip(50, 80, 70), Clip(31.5, 45, 60)]
    chosen = select_non_overlapping(cands, 3)
    assert [(c.start, c.end) for c in chosen] == [(0, 30), (50, 80), (31.5, 45)]


def test_heuristic_prefers_heatmap_and_hooks():
    tr = make_transcript()
    heat = [{"start_time": s, "end_time": s + 1, "value": 1.0 if 60 <= s < 80 else 0.1} for s in range(120)]
    sig = Signals(120, heat, [-30.0] * 120)
    cfg = ClipConfig(num_clips=2, min_duration=15, max_duration=25)
    clips = heuristic_select(tr, sig, 120, cfg)
    assert len(clips) == 2
    best = clips[0]
    assert 55 <= best.start and best.end <= 85
    assert cfg.min_duration <= best.duration <= cfg.max_duration
    assert best.end <= clips[1].start or best.start >= clips[1].end


def test_heuristic_without_transcript_uses_sliding_windows():
    loud = [-40.0] * 100
    for s in range(40, 60):
        loud[s] = -10.0
    sig = Signals(100, [], loud)
    cfg = ClipConfig(num_clips=1, min_duration=10, max_duration=20)
    clips = heuristic_select(Transcript([], []), sig, 100, cfg)
    assert len(clips) == 1 and 35 <= clips[0].start and clips[0].end <= 65


def test_fit_range_clamps_duration():
    segs = [Segment(i * 5.0, i * 5.0 + 4.5, f"s{i}") for i in range(20)]
    cfg = ClipConfig(min_duration=20, max_duration=30)
    i, j = _fit_range(segs, 3, 3, cfg)  # too short -> grow
    assert 20 <= segs[j].end - segs[i].start <= 30
    i, j = _fit_range(segs, 0, 15, cfg)  # too long -> shrink
    assert 20 <= segs[j].end - segs[i].start <= 30
    i, j = _fit_range(segs, 50, -2, cfg)  # out of range indexes are clamped
    assert 0 <= i <= j < len(segs)
