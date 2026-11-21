---
name: yt-clipper
description: Turn a YouTube video into short vertical clips with the most viral potential (TikTok, Reels, Shorts) - you watch the video via its transcript and frames, pick the moments yourself, render 9:16 clips with animated captions, and hand the files to the user. Use this whenever the user pastes a YouTube link (youtube.com, youtu.be, shorts) and wants clips, highlights, potongan, "klip viral", "bikin shorts/reels/tiktok dari video ini", "ambil bagian menarik", or asks you to watch/review a YouTube video to find its best moments - even if they don't say "clip" explicitly.
---

# YouTube viral clipper

The user gives you a YouTube link. You download and "watch" the video (transcript + frame
contact sheets + audience signals), decide which moments would work as standalone short-form
clips, render them with the `yt-clipper` CLI, and give the user the finished MP4 files.

You are the editor here: the CLI does the mechanical work (download, analysis, rendering) and
you make the editorial decisions. Reply in the user's language (often Indonesian).

## 0. Make sure the tools exist

```bash
yt-clipper --help >/dev/null 2>&1 && echo ok || echo missing
ffmpeg -version >/dev/null 2>&1 && echo ffmpeg-ok || echo ffmpeg-missing
```

- If `yt-clipper` is missing: inside this repo run `pip install -e .`; anywhere else run
  `pip install "git+https://github.com/hadijayyy/yt-clipper.git"`.
- If FFmpeg is missing, tell the user how to install it (`winget install Gyan.FFmpeg` on
  Windows, `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Linux) and stop until it is there.
- YouTube extraction needs a JavaScript runtime (`node` or `deno` on PATH). If downloads fail
  with "No supported JavaScript runtime", suggest installing Node.js.

## 1. Read the request

Pull out: the link, how many clips (default 3-5, fewer for videos under ~10 minutes), clip length
(default 20-60 s; Shorts max 60 s, TikTok/Reels can go to 90 s), and any focus the user asks for
("yang lucu", "tips bisnis", "skip sponsor"). Don't ask follow-up questions when defaults are
reasonable; mention the defaults you used at the end.

## 2. Prepare (download + analyze)

```bash
yt-clipper prepare "<URL>" --min <MIN> --max <MAX>
```

It prints `ANALYSIS_DIR=...` and the files to read. The download can take a few minutes for long
videos; run it with a generous timeout. The analysis directory contains:

| File | What it is |
|---|---|
| `overview.md` | title, duration, chapters, YouTube "most replayed" peaks, loudest moments, a heuristic shortlist |
| `transcript.txt` | the transcript as numbered segments: `[12] 64.4-71.2 (1:04) text...` |
| `frames/overview_*.jpg` | contact sheets: 16 frames per image, each labelled with its timestamp |
| `analysis.json`, `words.json` | machine data used by `render-plan` |

Common failures:
- **"YouTube blocked the download (bot check)"**: happens on servers/cloud sessions and sometimes
  at home. Retry with `--cookies-from-browser chrome` (or `firefox`, `edge`) if the user is logged
  in to YouTube in that browser on this machine, or ask for a `cookies.txt` and use `--cookies`.
  If you are running in a cloud container, tell the user that running Claude Code on their own
  computer avoids this.
- **No subtitles**: `prepare` falls back to Whisper if `faster-whisper` is installed
  (`pip install faster-whisper`; slow on CPU, roughly real-time for the `small` model). Without a
  transcript you can still pick clips from frames and audio signals, but there will be no captions.

## 3. Watch the video

Do this properly; clip quality depends entirely on how well you understand the content.

1. Read `overview.md`.
2. Read **all** of `transcript.txt`. For long videos read it in chunks, but don't skip parts:
   the best moment is often in the middle of a long podcast, far from the heatmap's obvious peak.
3. Look at every `frames/overview_*.jpg` to understand what is on screen: talking head vs.
   screen recording, B-roll, reactions, on-screen text, scene changes, where the speaker sits in
   the frame.
4. For each serious candidate, zoom in:
   ```bash
   yt-clipper frames <ANALYSIS_DIR> --start <s> --end <e> --count 12
   ```
   and view the printed image. This tells you whether the moment works visually and where the
   speaker is (needed for cropping, see step 4).

## 4. Pick the clips

Think like a short-form editor. A clip that performs:

- **Hooks in the first 1-3 seconds**: a bold claim, a question, a surprising number, conflict, or
  the setup of a punchline. If the strongest line is 10 s in, start the clip at that line.
- **Stands on its own**: a viewer with zero context understands it. Avoid clips that depend on
  "as I said earlier", or on a visual the viewer can't see.
- **Pays off**: an answer, punchline, reveal, or useful takeaway before the end, and ends on a
  finished sentence rather than mid-thought.
- **Carries emotion or value**: humour, strong opinion, controversy, a story with tension,
  concrete advice, quotable lines.
- Skip intros, "like and subscribe", sponsor reads, housekeeping, and outros.

Use the signals as evidence, not as the decision: a "most replayed" peak or a loud spike
(laughter, shouting) is a strong hint something happens there, so check what is said around it.
Clips must not overlap. Honour the user's focus if they gave one. If the video only has two good
moments, deliver two good clips and say so, rather than padding with weak ones.

Write `<ANALYSIS_DIR>/plan.json`:

```json
{
  "reframe": "blur",
  "clips": [
    {
      "start_segment": 41,
      "end_segment": 47,
      "title": "Kenapa 90% bisnis gagal di tahun pertama",
      "hook": "90% bisnis gagal karena ini",
      "reason": "Strong contrarian claim in the first line, concrete story, clear takeaway; replay peak at 12:40.",
      "score": 88
    },
    {
      "start": 1834.2,
      "end": 1879.5,
      "title": "Momen paling lucu",
      "hook": "Dia nggak nyangka jawabannya ini",
      "reason": "Laughter spike, self-contained setup and punchline.",
      "score": 81,
      "reframe": "crop",
      "crop_x": 0.35
    }
  ]
}
```

- Prefer `start_segment`/`end_segment` (inclusive segment numbers from `transcript.txt`): they
  land exactly on sentence boundaries. Use `start`/`end` in seconds when you need to start
  mid-segment; they are snapped to the nearest sentence boundary within 3 s.
- Keep each clip's duration (end time of `end_segment` minus start time of `start_segment`)
  inside the requested range.
- `title`: short and catchy, in the video's language. `hook`: the on-screen text shown at the top
  for the first ~3.5 s; make it punchy (max ~8 words). Order clips best first.
- **Framing** (`reframe`, plan-wide or per clip): `"blur"` (default) shows the whole 16:9 frame
  over a blurred background, safe for anything. `"crop"` fills the 9:16 screen and looks more
  native, but only use it when the important subject fits in a vertical slice; set `crop_x`
  (0 = left edge, 0.5 = centre, 1 = right edge) to where the speaker's face is in the frames you
  looked at. Two people talking on opposite sides, or slides/screen recordings: use `"blur"`.
- Other optional per-clip or plan-wide keys: `"aspect"` (`"vertical"`, `"square"`, `"original"`),
  `"captions": false`, `"hook_overlay": false`, `"caption_highlight": "#FFD400"`.

## 5. Render

```bash
yt-clipper render-plan <ANALYSIS_DIR>/plan.json -o output
```

Each clip takes roughly 10-30 s. Output per clip: `.mp4` (1080x1920, H.264/AAC, loudness
normalised), `.jpg` thumbnail, `.srt`; plus `manifest.json`. The command prints `OUTPUT_DIR=` and
the absolute path of every MP4.

Quick check before handing over: grab one frame from the middle of each clip and look at it,
e.g. `ffmpeg -v error -y -ss 5 -i <clip.mp4> -frames:v 1 -vf scale=360:-2 /tmp/check.jpg`.
If a crop cut the speaker's face off or captions cover something important, fix the plan and
re-render that clip.

## 6. Hand the clips to the user

- If you have a tool for sending files to the user (for example `SendUserFile`), send the MP4s
  with it so they can download them directly.
- Otherwise give the absolute path of each MP4 and offer to open the folder
  (`open <dir>` on macOS, `explorer <dir>` on Windows, `xdg-open <dir>` on Linux).

Then summarise, in the user's language, one entry per clip:

- title, time range in the original video (mm:ss-mm:ss) and duration
- why it should work (one sentence)
- a ready-to-post caption with 3-5 relevant hashtags

End with anything worth knowing: defaults you chose, moments you rejected and why, or problems
(no subtitles so no captions, a clip that was hard to frame).
