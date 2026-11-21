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

## Tes

```bash
pip install -e ".[dev,web,ai]"
pytest
```

Tes membuat video sintetis dengan ffmpeg, jadi tidak perlu akses internet.
