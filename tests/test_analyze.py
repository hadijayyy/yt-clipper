import json

import pytest

from yt_clipper.analyze import load_prepared, plan_to_clips, prepare, range_sheet, render_plan
from yt_clipper.cli import main
from yt_clipper.config import ClipConfig
from yt_clipper.download import local_source
from yt_clipper.media import probe_duration, probe_video_size


@pytest.fixture(scope="module")
def prepared(sample_media, tmp_path_factory):
    video, subs = sample_media
    work = tmp_path_factory.mktemp("work")
    cfg = ClipConfig(min_duration=8, max_duration=12, work_dir=work)
    return prepare(local_source(video, subs, "Demo"), cfg)


def test_prepare_writes_agent_readable_files(prepared):
    a = prepared
    for name in ("overview.md", "transcript.txt", "analysis.json", "words.json"):
        assert (a / name).exists(), name
    first = (a / "transcript.txt").read_text().splitlines()[0]
    assert first.startswith("[0] ") and "kita lanjut" in first
    overview = (a / "overview.md").read_text()
    assert "# Demo" in overview and "Heuristic shortlist" in overview and "Loudest" in overview
    state = json.loads((a / "analysis.json").read_text())
    assert state["sheets"] and all((a / s).exists() for s in state["sheets"])


def test_range_sheet(prepared):
    p = range_sheet(prepared, 20, 32, count=8)
    w, h = probe_video_size(p)
    assert w > h > 0  # 4 columns x 2 rows


def test_plan_to_clips_segments_seconds_and_errors(prepared):
    state, src, tr = load_prepared(prepared)
    plan = {"clips": [
        {"start_segment": 2, "end_segment": 4, "title": "A"},
        {"start": tr.segments[6].start + 1.0, "end": tr.segments[8].end - 1.0, "title": "B", "crop_x": 0.2},
    ]}
    (a, _), (b, ov) = plan_to_clips(plan, tr, src.duration)
    assert abs(a.start - (tr.segments[2].start - 0.15)) < 1e-6
    assert abs(a.end - (tr.segments[4].end + 0.35)) < 1e-6
    assert abs(b.start - (tr.segments[6].start - 0.15)) < 1e-6  # snapped to sentence start
    assert ov == {"crop_x": 0.2}
    with pytest.raises(ValueError):
        plan_to_clips({"clips": [{"title": "no times"}]}, tr, src.duration)
    with pytest.raises(ValueError):
        plan_to_clips({"clips": []}, tr, src.duration)


def test_render_plan_via_cli(prepared, tmp_path, capsys):
    _, _, tr = load_prepared(prepared)
    plan = prepared / "plan.json"
    plan.write_text(json.dumps({"clips": [
        {"start_segment": 5, "end_segment": 7, "title": "Rahasia", "hook": "Ternyata ini", "score": 90},
        {"start": 2.0, "end": 11.0, "title": "Intro", "reframe": "crop", "crop_x": 0.25, "aspect": "square"},
    ]}))
    out = tmp_path / "out"
    rc = main(["render-plan", str(plan), "-o", str(out), "--height", "640", "--preset", "ultrafast"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "OUTPUT_DIR=" in printed
    m = json.loads((out / "sample" / "manifest.json").read_text())
    assert [c["title"] for c in m["clips"]] == ["Rahasia", "Intro"]
    assert probe_video_size(m["clips"][0]["path"]) == (360, 640)
    assert probe_video_size(m["clips"][1]["path"]) == (360, 360)
    assert abs(probe_duration(m["clips"][0]["path"]) - m["clips"][0]["duration"]) < 0.3


def test_render_plan_rejects_invalid_override(prepared, tmp_path):
    plan = prepared / "bad_plan.json"
    plan.write_text(json.dumps({"clips": [{"start_segment": 1, "end_segment": 2, "reframe": "zoom"}]}))
    with pytest.raises(ValueError):
        render_plan(plan, ClipConfig(output_dir=tmp_path))
