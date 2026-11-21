from yt_clipper.captions import build_ass, chunk_words, hex_to_ass
from yt_clipper.config import ClipConfig
from yt_clipper.transcript import Word


def test_hex_to_ass():
    assert hex_to_ass("#FFD400") == "&H0000D4FF"


def test_chunking_respects_size_and_sentences():
    ws = [Word(i, i + 0.5, t) for i, t in enumerate(["a", "b", "c.", "d", "e", "f", "g"])]
    chunks = chunk_words(ws, 3)
    assert [[w.text for w in c] for c in chunks] == [["a", "b", "c."], ["d", "e", "f"], ["g"]]


def test_build_ass_relative_times_and_highlight():
    ws = [Word(10.0, 10.4, "halo"), Word(10.4, 10.9, "dunia{x}")]
    ass = build_ass(ws, 10.0, 20.0, 1080, 1920, ClipConfig(), hook="Judul")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:00.40,Caption" in ass
    assert "\\c&H00D4FF&" in ass
    assert "DUNIA(X)" in ass  # braces escaped so they can't inject override tags
    assert ",Hook,," in ass and "Judul" in ass
