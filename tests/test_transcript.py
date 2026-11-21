from pathlib import Path

from yt_clipper.download import extract_video_id
from yt_clipper.transcript import build_segments, parse_vtt

from conftest import write_auto_vtt


def test_extract_video_id():
    vid = "dQw4w9WgXcQ"
    for url in [
        f"https://www.youtube.com/watch?v={vid}",
        f"https://youtube.com/watch?feature=share&v={vid}&t=10",
        f"https://youtu.be/{vid}?si=abc",
        f"https://www.youtube.com/shorts/{vid}",
        f"https://www.youtube.com/live/{vid}",
        vid,
    ]:
        assert extract_video_id(url) == vid
    assert extract_video_id("https://example.com/video") is None


def test_parse_auto_vtt_word_timings(tmp_path):
    p = write_auto_vtt(tmp_path / "a.vtt", [(1.0, ["halo", "semua", "apa", "kabar?"]),
                                             (3.0, ["hari", "ini", "kita", "belajar."])])
    words = parse_vtt(p)
    assert [w.text for w in words] == ["halo", "semua", "apa", "kabar?", "hari", "ini", "kita", "belajar."]
    assert abs(words[0].start - 1.0) < 1e-6
    assert abs(words[1].start - 1.4) < 1e-6
    assert abs(words[3].end - 2.6) < 1e-6  # last word ends at cue end
    assert all(w.end > w.start for w in words)


def test_parse_plain_vtt(tmp_path):
    p = tmp_path / "b.vtt"
    p.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello &amp; welcome\n\n"
        "00:00:03.000 --> 00:00:05.000\n<i>to the show</i> [Music]\n",
        encoding="utf-8",
    )
    words = parse_vtt(p)
    assert [w.text for w in words] == ["Hello", "&", "welcome", "to", "the", "show"]
    assert words[0].start == 1.0 and abs(words[2].end - 3.0) < 1e-6


def test_segments_split_on_sentence_and_pause(tmp_path):
    p = write_auto_vtt(tmp_path / "c.vtt", [(0.0, ["satu", "dua", "tiga", "empat."]),
                                             (1.7, ["lima", "enam"]),
                                             (5.0, ["tujuh", "delapan"])])
    segs = build_segments(parse_vtt(p))
    assert [s.text for s in segs] == ["satu dua tiga empat.", "lima enam", "tujuh delapan"]
