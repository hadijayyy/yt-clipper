# yt-clipper

Clipping engine: cukup tempel **link YouTube**, hasilnya beberapa klip pendek
(9:16 untuk TikTok / Reels / Shorts) dengan caption animasi kata per kata.

```
link YouTube ──► download (yt-dlp) ──► transkrip ──► cari momen terbaik ──► render (ffmpeg)
                 video + subtitle +    subtitle YT    Claude AI atau          crop / blur 9:16
                 heatmap "most         atau Whisper   skor heuristik          + caption + hook
                 replayed"                                                      + loudnorm
```

## Fitur

- **Input cuma link YouTube** (watch, youtu.be, shorts, live). Ada juga `--video` untuk file lokal.
- **Pemilihan momen otomatis**
  - **AI (Claude)**: membaca transkrip, judul, chapter, dan heatmap "most replayed", lalu memilih
    klip yang punya hook kuat, bisa berdiri sendiri, dan punya payoff. Juga membuat judul dan teks hook.
  - **Heuristik** (tanpa API key): skor gabungan dari heatmap YouTube, energi audio (loudness),
    kepadatan bicara, kata-kata pemicu (ID + EN), dan kekuatan 5 detik pertama.
  - Mode `auto` memakai Claude kalau ada kredensial, kalau tidak otomatis pakai heuristik.
- **Potongan rapi**: klip selalu mulai dan berakhir di batas kalimat, durasi dijaga di rentang min–maks,
  tidak ada klip yang tumpang tindih.
- **Reframe**: `blur` (video utuh di tengah, latar blur) atau `crop` (penuh, posisi bisa diatur), format vertikal / kotak / asli.
- **Caption animasi**: kata yang sedang diucapkan disorot (warna bisa diatur), plus teks hook di atas
  selama 3,5 detik pertama. File `.srt` ikut dibuat untuk upload manual.
- **Audio dinormalisasi** ke -14 LUFS (standar platform), dengan fade in/out singkat.
- **Skill Claude Code**: tempel link di Claude Code, Claude menonton video (transkrip + frame), memilih klip, dan memberi file-nya.
- **Bot Telegram**: kirim link ke bot, klipnya dikirim balik dan bisa langsung diunduh.
- **Web UI** sederhana dan **CLI**. Output: MP4 + thumbnail + SRT + `manifest.json`.

## Instalasi

Butuh Python 3.10+ dan [FFmpeg](https://ffmpeg.org/) (dengan libass, sudah default di kebanyakan build).

```bash
pip install -e ".[all]"     # yt-dlp + Claude SDK + web UI + Whisper
# atau minimal:
pip install -e .            # hanya CLI + mode heuristik
```

YouTube sekarang butuh JavaScript runtime untuk ekstraksi; install [deno](https://deno.com/)
atau pastikan `node` ada di PATH (otomatis dideteksi).

Untuk mode AI, set API key Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Pakai

### Web UI

```bash
yt-clipper serve            # buka http://127.0.0.1:8000
```

Tempel link, klik **Buat klip**, tunggu, lalu putar/unduh hasilnya.

### Skill Claude Code (Claude yang menonton dan memilih)

Repo ini menyertakan skill di `.claude/skills/yt-clipper/`. Buka Claude Code di folder repo ini,
lalu tempel link:

```
https://youtu.be/VIDEO_ID bikinin 3 klip viral, fokus ke bagian yang lucu
```

Claude Code akan: menjalankan `yt-clipper prepare` (download, transkrip, lembar frame), membaca
seluruh transkrip dan melihat frame-nya, memilih momen sendiri (termasuk judul, hook, dan posisi
crop), merender dengan `yt-clipper render-plan`, lalu memberi file MP4 plus caption siap posting.
Mode ini tidak butuh `ANTHROPIC_API_KEY`, karena Claude Code sendiri yang memilih.

Supaya skill bisa dipakai dari folder mana saja, salin ke folder skill pribadi:

```bash
# macOS / Linux
mkdir -p ~/.claude/skills && cp -r .claude/skills/yt-clipper ~/.claude/skills/
# Windows (PowerShell)
Copy-Item -Recurse .claude\skills\yt-clipper $HOME\.claude\skills\
```

Perintah yang dipakai skill ini juga bisa dijalankan manual:

```bash
yt-clipper prepare URL                       # -> work/<id>/analysis/ (overview.md, transcript.txt, frames/)
yt-clipper frames work/<id>/analysis --start 60 --end 90   # lembar frame untuk rentang waktu
yt-clipper render-plan work/<id>/analysis/plan.json        # render klip dari rencana JSON
```

### Bot Telegram

Alurnya: kirim link YouTube ke bot, bot mengambil video dan memilih momen paling berpotensi viral,
lalu mengirim klipnya balik ke chat. Tekan klip, lalu **Simpan / Download**.

**1. Buat bot (sekali saja)**
1. Di Telegram, buka **@BotFather**, kirim `/newbot`, beri nama.
2. BotFather memberi **token** seperti `123456789:AAH...`. Simpan, jangan dibagikan.

**2. Jalankan bot di komputermu**

```bash
pip install -e ".[ai,telegram]"
yt-clipper telegram --token 123456789:AAH...
```

Kirim `/id` ke bot untuk melihat user ID kamu, lalu jalankan ulang dengan pembatasan akses
supaya orang lain tidak bisa memakai bot (dan kuota API) kamu:

```bash
yt-clipper telegram --token 123456789:AAH... --allowed-users 111111111
```

Token dan daftar user juga bisa lewat environment variable `TELEGRAM_BOT_TOKEN` dan
`TELEGRAM_ALLOWED_USERS` (pisahkan dengan koma).

**3. Pakai**

| Pesan ke bot | Hasil |
|---|---|
| `https://youtu.be/VIDEO_ID` | klip dengan pengaturan default (3 klip, 20–60 dtk) |
| `https://youtu.be/VIDEO_ID 5` | 5 klip |
| `https://youtu.be/VIDEO_ID 3 30-45` | 3 klip, 30–45 detik |
| `https://youtu.be/VIDEO_ID 3 30-45 fokus ke tips bisnis` | plus instruksi untuk AI |
| `/status` | lihat yang sedang diproses dan antrean |

Bot menampilkan progres di satu pesan yang terus diperbarui. Permintaan diproses satu per satu
(antrean). Opsi tambahan: `--cleanup` (hapus file setelah terkirim, hemat disk), `-n`, `--min`,
`--max`, `--reframe crop`, `--cookies` (lihat `yt-clipper telegram --help`).

Catatan: bot Telegram hanya boleh mengirim file maksimal 50 MB. Klip yang lebih besar otomatis
dikompres ulang (720p) sebelum dikirim.

Bot hanya jalan selama terminalnya terbuka. Agar online 24 jam, jalankan di VPS (perlu `--cookies`
karena YouTube sering memblokir IP server).

### CLI

```bash
# 5 klip vertikal 20–60 detik
yt-clipper "https://www.youtube.com/watch?v=VIDEO_ID"

# 3 klip 30–45 detik, crop penuh, dengan arahan untuk AI
yt-clipper "https://youtu.be/VIDEO_ID" -n 3 --min 30 --max 45 --reframe crop \
  --prompt "fokus ke tips bisnis, hindari bagian sponsor"

# tanpa AI, tanpa caption, format kotak
yt-clipper URL --selector heuristic --no-captions --aspect square

# file lokal + subtitle
yt-clipper --video podcast.mp4 --subs podcast.vtt
```

Hasil ada di `output/<video_id>/`:

```
clip_01_judul-klip.mp4   clip_01_judul-klip.jpg   clip_01_judul-klip.srt   ...   manifest.json
```

`manifest.json` berisi waktu mulai/selesai, skor, judul, hook, alasan pemilihan, dan link
ke detik yang sama di YouTube.

Semua opsi: `yt-clipper --help`. Yang paling sering dipakai:

| Opsi | Default | Keterangan |
|---|---|---|
| `-n, --num-clips` | 5 | jumlah klip |
| `--min / --max` | 20 / 60 | durasi klip (detik) |
| `--selector` | auto | `auto`, `ai`, `heuristic` |
| `--aspect` | vertical | `vertical`, `square`, `original` |
| `--reframe` | blur | `blur` atau `crop` (`--crop-x 0..1` untuk geser) |
| `--transcript` | auto | `auto` (subtitle YT lalu Whisper), `youtube`, `whisper`, `none` |
| `--lang` | id,en | urutan bahasa subtitle yang dicari |
| `--highlight` | #FFD400 | warna kata aktif di caption |
| `--cookies` | – | `cookies.txt` jika YouTube meminta login / cek bot |

### Kalau YouTube memblokir ("Sign in to confirm you're not a bot")

Biasanya terjadi di server/VPS. Ekspor cookies dari browser yang sudah login YouTube:

```bash
yt-clipper URL --cookies cookies.txt
yt-clipper URL --cookies-from-browser chrome
```

Untuk web UI, set `YT_CLIPPER_COOKIES=/path/cookies.txt` (atau `YT_CLIPPER_COOKIES_FROM_BROWSER=chrome`)
sebelum `yt-clipper serve`.

## Pakai sebagai library

```python
from yt_clipper import ClipConfig, run_pipeline

manifest = run_pipeline(
    "https://www.youtube.com/watch?v=VIDEO_ID",
    ClipConfig(num_clips=3, min_duration=25, max_duration=45, reframe="crop"),
)
for clip in manifest["clips"]:
    print(clip["title"], clip["start"], clip["file"])
```

## Struktur kode

| File | Isi |
|---|---|
| `yt_clipper/download.py` | yt-dlp: video, subtitle, heatmap, chapter |
| `yt_clipper/transcript.py` | parser WebVTT (termasuk auto-caption YouTube dengan timing per kata), Whisper |
| `yt_clipper/highlights.py` | sinyal, skor heuristik, pemilihan dengan Claude |
| `yt_clipper/captions.py` | caption ASS animasi kata per kata + teks hook |
| `yt_clipper/render.py` | ffmpeg: potong, reframe, burn caption, loudnorm, thumbnail |
| `yt_clipper/pipeline.py` | alur lengkap + `manifest.json` |
| `yt_clipper/web.py`, `static/index.html` | web UI + JSON API (`POST /api/jobs`, `GET /api/jobs/{id}`) |
| `yt_clipper/telegram_bot.py` | bot Telegram (antrean, progres, kirim klip) |
| `yt_clipper/analyze.py` | `prepare` / `frames` / `render-plan` untuk skill Claude Code |
| `.claude/skills/yt-clipper/SKILL.md` | skill Claude Code |

## Tes

```bash
pip install -e ".[dev,web,ai,telegram]"
pytest
```

Tes membuat video sintetis dengan ffmpeg, jadi tidak perlu akses internet.
