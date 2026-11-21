import json
import time

from yt_clipper import pipeline
from yt_clipper.config import ClipConfig
from yt_clipper.download import local_source
from yt_clipper.media import probe_video_size, probe_duration


def _cfg(tmp_path, **kw):
    base = dict(num_clips=2, min_duration=8, max_duration=12, output_height=640,
                selector="heuristic", preset="ultrafast",
                output_dir=tmp_path / "out", work_dir=tmp_path / "work")
    base.update(kw)
    return ClipConfig(**base)


def test_end_to_end_vertical(sample_media, tmp_path):
    video, subs = sample_media
    m = pipeline.run_pipeline(local_source(video, subs), _cfg(tmp_path))
    assert m["transcript_source"] == "youtube"
    assert len(m["clips"]) == 2
    out = tmp_path / "out" / "sample"
    best = m["clips"][0]
    assert 15 <= best["start"] and best["end"] <= 37  # the loud, hook-heavy section
    for c in m["clips"]:
        f = out / c["file"]
        assert probe_video_size(f) == (360, 640)
        assert 7.5 <= probe_duration(f) <= 12.5
        assert (out / c["thumbnail"]).exists() and (out / c["srt"]).exists()
    assert json.loads((out / "manifest.json").read_text())["clips"][0]["file"] == best["file"]


def test_end_to_end_crop_no_transcript(sample_media, tmp_path):
    video, _ = sample_media
    m = pipeline.run_pipeline(
        local_source(video), _cfg(tmp_path, num_clips=1, reframe="crop", transcript="none", aspect="square")
    )
    c = m["clips"][0]
    assert c["srt"] is None
    assert probe_video_size(tmp_path / "out" / "sample" / c["file"]) == (360, 360)


def test_web_api(sample_media, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from yt_clipper import web

    video, subs = sample_media
    monkeypatch.setattr(pipeline, "download", lambda url, cfg: local_source(video, subs, "Demo"))
    app = web.create_app(tmp_path / "out", tmp_path / "work")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.post("/api/jobs", json={"url": "https://example.com/x"}).status_code == 400

    r = client.post("/api/jobs", json={
        "url": "https://youtu.be/dQw4w9WgXcQ", "num_clips": 1, "min_duration": 8,
        "max_duration": 12, "selector": "heuristic",
    })
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(240):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert job["status"] == "done", job
    clip = job["result"]["clips"][0]
    assert client.get(clip["video_url"]).status_code == 200
    assert job["result"]["title"] == "Demo"
