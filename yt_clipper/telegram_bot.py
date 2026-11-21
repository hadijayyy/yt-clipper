"""Telegram bot: send a YouTube link, receive the viral-potential clips back.

Run with:  yt-clipper telegram --token <BOT_TOKEN> --allowed-users <your_user_id>
"""

from __future__ import annotations

import asyncio
import html
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from .config import ClipConfig
from .download import extract_video_id
from .log import log, set_listener

# Bots on the public Bot API may upload at most 50 MB per file.
TELEGRAM_MAX_BYTES = 49 * 1024 * 1024
CAPTION_LIMIT = 1024

URL_RE = re.compile(r"(https?://\S+|(?:www\.)?(?:youtube\.com|youtu\.be)/\S+)", re.I)
RANGE_RE = re.compile(r"^(\d{1,3})\s*-\s*(\d{1,3})s?$")

HELP_TEXT = (
    "Kirim link YouTube, nanti aku ambil momen yang paling berpotensi viral "
    "dan kirim balik klip vertikal lengkap dengan caption.\n\n"
    "<b>Format pesan</b>\n"
    "<code>LINK</code>\n"
    "<code>LINK 3</code> → 3 klip\n"
    "<code>LINK 3 30-45</code> → 3 klip, durasi 30–45 detik\n"
    "<code>LINK 3 30-45 fokus ke tips bisnis</code> → plus instruksi untuk AI\n\n"
    "<b>Perintah</b>\n"
    "/start, /help: bantuan ini\n"
    "/id: lihat user ID Telegram kamu\n"
    "/status: lihat antrean"
)


@dataclass
class ClipRequest:
    url: str
    num_clips: int | None = None
    min_duration: float | None = None
    max_duration: float | None = None
    instructions: str = ""


def parse_request(text: str) -> ClipRequest | None:
    """Parse "LINK [jumlah] [min-max] [instruksi...]". Returns None if no YouTube link."""
    text = (text or "").strip()
    m = URL_RE.search(text)
    if not m or not extract_video_id(m.group(1)):
        return None
    url = m.group(1)
    if not url.lower().startswith("http"):
        url = "https://" + url
    req = ClipRequest(url=url)
    rest = (text[: m.start()] + " " + text[m.end():]).split()
    leftover: list[str] = []
    for i, tok in enumerate(rest):
        rng = RANGE_RE.match(tok)
        if req.num_clips is None and not leftover and tok.isdigit() and 1 <= int(tok) <= 10:
            req.num_clips = int(tok)
        elif req.min_duration is None and not leftover and rng:
            a, b = sorted((int(rng.group(1)), int(rng.group(2))))
            req.min_duration, req.max_duration = float(max(a, 5)), float(max(b, a, 5))
        else:
            leftover = rest[i:]
            break
    req.instructions = " ".join(leftover)[:500]
    return req


def _fmt_t(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


def clip_caption(clip: dict, total: int) -> str:
    e = html.escape
    lines = [f"<b>{clip['index']}/{total}. {e(clip.get('title') or 'Klip')}</b>"]
    if clip.get("hook"):
        lines.append(f"Hook: {e(clip['hook'])}")
    lines.append(
        f"Skor {round(clip.get('score', 0))} · {_fmt_t(clip['start'])}–{_fmt_t(clip['end'])} "
        f"({clip['duration']:.0f} dtk)"
    )
    if clip.get("reason") and not clip["reason"].startswith("heuristic"):
        lines.append(f"<i>{e(clip['reason'])}</i>")
    if clip.get("source_url"):
        lines.append(f'<a href="{e(clip["source_url"])}">Lihat di YouTube</a>')
    text = "\n".join(lines)
    if len(text) > CAPTION_LIMIT:
        # Drop the reason first, then hard-trim the title line if still too long.
        lines = [ln for ln in lines if not ln.startswith("<i>")]
        text = "\n".join(lines)[:CAPTION_LIMIT]
    return text


def fit_for_telegram(path: Path) -> Path:
    """Return a file under Telegram's upload limit, re-encoding a smaller copy if needed."""
    from .media import probe_video_size, run

    path = Path(path)
    if path.stat().st_size <= TELEGRAM_MAX_BYTES:
        return path
    w, h = probe_video_size(path)
    small = path.with_name(path.stem + "_tg.mp4")
    for crf, short_side in ((26, 720), (30, 540)):
        scale = f"scale={short_side}:-2" if w <= h else f"scale=-2:{short_side}"
        run([
            "ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-i", str(path),
            "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small),
        ])
        if small.stat().st_size <= TELEGRAM_MAX_BYTES:
            return small
    return small


class ClipperBot:
    def __init__(self, base_cfg: ClipConfig, allowed_users: set[int] | None = None, cleanup: bool = False):
        self.base_cfg = base_cfg
        self.allowed = allowed_users or set()
        self.cleanup = cleanup
        self.executor = ThreadPoolExecutor(max_workers=1)  # one render job at a time
        self.lock = asyncio.Lock()
        self.waiting = 0
        self.current: str | None = None

    # ------------------------------------------------------------------ helpers
    def is_allowed(self, user_id: int | None) -> bool:
        return not self.allowed or (user_id is not None and user_id in self.allowed)

    def make_config(self, req: ClipRequest) -> ClipConfig:
        cfg = replace(self.base_cfg)
        if req.num_clips:
            cfg.num_clips = req.num_clips
        if req.min_duration:
            cfg.min_duration, cfg.max_duration = req.min_duration, req.max_duration
        if req.instructions:
            cfg.ai_instructions = req.instructions
        cfg.validate()
        return cfg

    # ----------------------------------------------------------------- handlers
    async def cmd_help(self, update, context) -> None:
        await update.effective_message.reply_html(HELP_TEXT, disable_web_page_preview=True)

    async def cmd_id(self, update, context) -> None:
        uid = update.effective_user.id if update.effective_user else None
        await update.effective_message.reply_html(f"User ID kamu: <code>{uid}</code>")

    async def cmd_status(self, update, context) -> None:
        if self.current:
            msg = f"Sedang memproses: {html.escape(self.current)}\nMenunggu di antrean: {self.waiting}"
        else:
            msg = "Tidak ada yang sedang diproses."
        await update.effective_message.reply_html(msg, disable_web_page_preview=True)

    async def on_message(self, update, context) -> None:
        message = update.effective_message
        user = update.effective_user
        if not self.is_allowed(user.id if user else None):
            await message.reply_html(
                "Maaf, kamu belum diizinkan memakai bot ini.\n"
                f"User ID kamu: <code>{user.id if user else '?'}</code> (kirim ke pemilik bot)."
            )
            return

        text = message.text or message.caption or ""
        text = re.sub(r"^/clip(@\w+)?", "", text)
        req = parse_request(text)
        if not req:
            await message.reply_html("Kirim link YouTube ya.\n\n" + HELP_TEXT, disable_web_page_preview=True)
            return
        try:
            cfg = self.make_config(req)
        except ValueError as e:
            await message.reply_text(f"Pengaturan tidak valid: {e}")
            return

        log(f"Telegram request from {user.id if user else '?'}: {req.url}")
        status = await message.reply_html(
            self._status_text(req, cfg, "Masuk antrean..." if self.lock.locked() else "Mulai..."),
            disable_web_page_preview=True,
        )
        self.waiting += 1
        async with self.lock:
            self.waiting -= 1
            self.current = req.url
            try:
                await self._process(req, cfg, message, status, context)
            finally:
                self.current = None

    # ------------------------------------------------------------------ worker
    def _status_text(self, req: ClipRequest, cfg: ClipConfig, line: str) -> str:
        return (
            f"<b>Memproses</b> {html.escape(req.url)}\n"
            f"{cfg.num_clips} klip · {cfg.min_duration:.0f}–{cfg.max_duration:.0f} dtk\n\n"
            f"<code>{html.escape(line)}</code>"
        )

    async def _safe_edit(self, status, text: str) -> None:
        from telegram.error import BadRequest, RetryAfter

        try:
            await status.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
        except RetryAfter as e:
            await asyncio.sleep(float(getattr(e, "retry_after", 3)))
        except BadRequest:
            pass  # "message is not modified" and similar

    async def _process(self, req: ClipRequest, cfg: ClipConfig, message, status, context) -> None:
        from .pipeline import run_pipeline

        state = {"last": "Mulai..."}

        def job():
            set_listener(lambda msg: state.__setitem__("last", msg))
            try:
                return run_pipeline(req.url, cfg)
            finally:
                set_listener(None)

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self.executor, job)
        shown = None
        while not fut.done():
            if state["last"] != shown:
                shown = state["last"]
                await self._safe_edit(status, self._status_text(req, cfg, shown))
            await asyncio.wait({fut}, timeout=4)

        try:
            manifest = fut.result()
        except Exception as e:
            log(f"Telegram job failed: {e}")
            await self._safe_edit(status, f"<b>Gagal</b> memproses {html.escape(req.url)}\n\n{html.escape(str(e))}")
            return

        clips = manifest["clips"]
        out_dir = Path(manifest["output_dir"])
        await self._safe_edit(
            status,
            f"<b>Selesai</b>: {html.escape(manifest['title'])}\n"
            f"{len(clips)} klip dalam {manifest['elapsed_sec']:.0f} dtk. Mengirim...",
        )
        chat_id = message.chat_id
        for clip in clips:
            video_path = await loop.run_in_executor(self.executor, fit_for_telegram, out_dir / clip["file"])
            thumb = out_dir / clip["thumbnail"]
            try:
                with open(video_path, "rb") as vf, open(thumb, "rb") as tf:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=vf,
                        thumbnail=tf,
                        caption=clip_caption(clip, len(clips)),
                        parse_mode="HTML",
                        supports_streaming=True,
                        width=clip.get("width"),
                        height=clip.get("height"),
                        duration=int(round(clip["duration"])),
                        filename=clip["file"],
                        read_timeout=300,
                        write_timeout=300,
                    )
            except Exception as e:
                log(f"Sending {clip['file']} failed: {e}")
                await message.reply_text(f"Gagal mengirim klip {clip['index']}: {e}")
        await self._safe_edit(
            status,
            f"<b>Selesai</b>: {html.escape(manifest['title'])}\n"
            f"{len(clips)} klip terkirim. Tekan klip lalu Simpan/Download untuk mengunduh.",
        )
        if self.cleanup:
            shutil.rmtree(out_dir, ignore_errors=True)
            shutil.rmtree(Path(cfg.work_dir) / manifest["video_id"], ignore_errors=True)


def run_bot(token: str, base_cfg: ClipConfig, allowed_users: set[int] | None = None, cleanup: bool = False) -> None:
    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters
    except ImportError as e:
        raise RuntimeError("Install the Telegram library: pip install 'python-telegram-bot>=21'") from e

    base_cfg.validate()
    bot = ClipperBot(base_cfg, allowed_users, cleanup)
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .read_timeout(30)
        .write_timeout(60)
        .media_write_timeout(300)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], bot.cmd_help))
    app.add_handler(CommandHandler("id", bot.cmd_id))
    app.add_handler(CommandHandler("status", bot.cmd_status))
    app.add_handler(CommandHandler("clip", bot.on_message, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_message, block=False))

    if not allowed_users:
        log("WARNING: no --allowed-users set; anyone who finds the bot can use it.")
    log("Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)
