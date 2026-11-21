import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from yt_clipper import pipeline, telegram_bot
from yt_clipper.config import ClipConfig
from yt_clipper.download import local_source
from yt_clipper.media import probe_video_size
from yt_clipper.telegram_bot import ClipperBot, clip_caption, fit_for_telegram, parse_request


def test_parse_request_variants():
    r = parse_request("https://youtu.be/dQw4w9WgXcQ")
    assert r.url == "https://youtu.be/dQw4w9WgXcQ" and r.num_clips is None and r.instructions == ""

    r = parse_request("https://www.youtube.com/watch?v=dQw4w9WgXcQ 3 30-45 fokus ke tips bisnis")
    assert (r.num_clips, r.min_duration, r.max_duration) == (3, 30.0, 45.0)
    assert r.instructions == "fokus ke tips bisnis"

    r = parse_request("tolong clip ini youtu.be/dQw4w9WgXcQ 2")
    assert r.url == "https://youtu.be/dQw4w9WgXcQ" and r.num_clips is None
    assert r.instructions.startswith("tolong clip ini")

    r = parse_request("https://youtu.be/dQw4w9WgXcQ 45-30")
    assert (r.min_duration, r.max_duration) == (30.0, 45.0)

    assert parse_request("halo bot") is None
    assert parse_request("https://example.com/video") is None


def test_caption_escapes_html_and_fits_limit():
    clip = {"index": 1, "title": "<b>Tips & trik</b>", "hook": "x" * 2000, "score": 88.4,
            "start": 64, "end": 90, "duration": 26, "reason": "y" * 50,
            "source_url": "https://youtu.be/abc?t=64"}
    cap = clip_caption(clip, 3)
    assert "&lt;b&gt;Tips &amp; trik&lt;/b&gt;" in cap
    assert len(cap) <= 1024
    short = clip_caption({**clip, "hook": "Ternyata..."}, 3)
    assert "1:04–1:30" in short and "Skor 88" in short and "<i>" in short


def test_fit_for_telegram_shrinks_large_files(sample_media, tmp_path, monkeypatch):
    video, _ = sample_media
    assert fit_for_telegram(video) == video  # small file untouched
    monkeypatch.setattr(telegram_bot, "TELEGRAM_MAX_BYTES", 50_000)
    big = tmp_path / "big.mp4"
    big.write_bytes(video.read_bytes())
    small = fit_for_telegram(big)
    assert small != big and small.exists()
    assert min(probe_video_size(small)) <= 720


class FakeStatus:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class FakeMessage:
    def __init__(self, text):
        self.text, self.caption, self.chat_id = text, None, 42
        self.replies, self.status = [], FakeStatus()

    async def reply_html(self, text, **kw):
        self.replies.append(text)
        return self.status

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self.status


class FakeBot:
    def __init__(self):
        self.videos = []

    async def send_video(self, chat_id, video, thumbnail, caption, **kw):
        self.videos.append({"chat_id": chat_id, "size": len(video.read()),
                            "thumb": len(thumbnail.read()), "caption": caption, **kw})


def _update(text, user_id=7):
    msg = FakeMessage(text)
    return SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=user_id)), msg


def _cfg(tmp_path):
    return ClipConfig(num_clips=1, min_duration=8, max_duration=12, output_height=640,
                      selector="heuristic", preset="ultrafast",
                      output_dir=tmp_path / "out", work_dir=tmp_path / "work")


def test_bot_end_to_end_sends_clips(sample_media, tmp_path, monkeypatch):
    video, subs = sample_media
    monkeypatch.setattr(pipeline, "download", lambda url, cfg: local_source(video, subs, "Demo <video>"))
    bot = ClipperBot(_cfg(tmp_path), allowed_users={7}, cleanup=True)
    fake_bot = FakeBot()
    update, msg = _update("https://youtu.be/dQw4w9WgXcQ 2 8-12")
    asyncio.run(bot.on_message(update, SimpleNamespace(bot=fake_bot)))

    assert len(fake_bot.videos) == 2
    v = fake_bot.videos[0]
    assert v["chat_id"] == 42 and v["size"] > 10_000 and v["thumb"] > 0
    assert v["supports_streaming"] and v["width"] == 360 and v["height"] == 640
    assert v["caption"].startswith("<b>1/2.")
    assert "Selesai" in msg.status.edits[-1] and "Demo &lt;video&gt;" in msg.status.edits[-1]
    assert not (tmp_path / "out" / "sample").exists()  # cleanup removed outputs


def test_bot_rejects_unknown_users_and_bad_input(tmp_path):
    bot = ClipperBot(_cfg(tmp_path), allowed_users={7})
    update, msg = _update("https://youtu.be/dQw4w9WgXcQ", user_id=99)
    asyncio.run(bot.on_message(update, SimpleNamespace(bot=FakeBot())))
    assert "belum diizinkan" in msg.replies[0] and "99" in msg.replies[0]

    update, msg = _update("halo")
    asyncio.run(bot.on_message(update, SimpleNamespace(bot=FakeBot())))
    assert "Kirim link YouTube" in msg.replies[0]


def test_bot_reports_pipeline_errors(tmp_path, monkeypatch):
    def boom(url, cfg):
        raise RuntimeError("YouTube blocked the download (bot check).")

    monkeypatch.setattr(pipeline, "download", boom)
    bot = ClipperBot(_cfg(tmp_path))
    update, msg = _update("https://youtu.be/dQw4w9WgXcQ")
    fake_bot = FakeBot()
    asyncio.run(bot.on_message(update, SimpleNamespace(bot=fake_bot)))
    assert not fake_bot.videos
    assert "Gagal" in msg.status.edits[-1] and "bot check" in msg.status.edits[-1]
