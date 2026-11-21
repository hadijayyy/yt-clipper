import json
from types import SimpleNamespace

import pytest

from yt_clipper.config import ClipConfig
from yt_clipper.highlights import Signals, find_highlights
from yt_clipper.transcript import Transcript, Word, build_segments


def transcript():
    words = [Word(s + 0.1, s + 0.8, f"w{s}" + ("." if s % 5 == 4 else "")) for s in range(200)]
    return Transcript(words, build_segments(words))


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.kwargs = payload, stop_reason, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def install_fake(monkeypatch, fake):
    import anthropic

    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)


def test_ai_select_maps_segments_and_fits_duration(monkeypatch):
    tr = transcript()
    payload = {"clips": [
        {"start_segment": 10, "end_segment": 13, "title": "Momen terbaik", "hook": "Ternyata...",
         "reason": "strong payoff", "score": 92},
        {"start_segment": 11, "end_segment": 12, "title": "Overlap", "hook": "", "reason": "", "score": 50},
        {"start_segment": 30, "end_segment": 30, "title": "Terlalu pendek", "hook": "", "reason": "", "score": 70},
    ]}
    fake = FakeMessages(payload)
    install_fake(monkeypatch, fake)
    cfg = ClipConfig(num_clips=3, min_duration=15, max_duration=25, selector="ai")
    clips = find_highlights(tr, Signals(200, [], []), {"title": "T", "duration": 200}, cfg)

    assert [c.title for c in clips] == ["Momen terbaik", "Terlalu pendek"]  # overlap dropped
    for c in clips:
        assert 15 <= c.duration <= 25
    assert clips[0].start == tr.segments[10].start

    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default"
    assert "[10]" in kw["messages"][0]["content"]


def test_ai_refusal_falls_back_in_auto_mode(monkeypatch):
    install_fake(monkeypatch, FakeMessages({"clips": []}, stop_reason="refusal"))
    cfg = ClipConfig(num_clips=2, min_duration=15, max_duration=25, selector="auto")
    clips = find_highlights(transcript(), Signals(200, [], []), {"duration": 200}, cfg)
    assert len(clips) == 2 and clips[0].reason.startswith("heuristic")


def test_ai_mode_raises_on_refusal(monkeypatch):
    install_fake(monkeypatch, FakeMessages({"clips": []}, stop_reason="refusal"))
    cfg = ClipConfig(num_clips=2, min_duration=15, max_duration=25, selector="ai")
    with pytest.raises(RuntimeError, match="declined"):
        find_highlights(transcript(), Signals(200, [], []), {"duration": 200}, cfg)
