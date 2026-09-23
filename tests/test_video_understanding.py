from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from funnel_growth_agent import video_analysis as video
from funnel_growth_agent.gemini import analyze_creative
from funnel_growth_agent.models import CreativeAnalysis, RankedCreative


def observation(start=0, end=10, audio=True):
    return {
        "visualHook": "Blank opening followed by a live product demonstration",
        "primaryPromise": "Create consistent icons",
        "audienceIntent": "Brand designers",
        "ctaIntent": "Try the editor",
        "suggestedLandingTheme": "Consistent icon workflow",
        "visibleText": ["TRY IT"],
        "productClaims": ["The ad claims consistent results"],
        "videoEvidence": {
            "concept": "One design becomes a consistent set",
            "narrative": "A blank opening leads to a demonstration and a closing CTA.",
            "moments": [
                {
                    "start": start + 1,
                    "end": start + 2,
                    "kind": "demonstration",
                    "description": "The cursor edits an icon",
                    "visibleText": ["EDIT"],
                    "spokenText": ["Make an entire family"] if audio else [],
                },
                {
                    "start": end - 1,
                    "end": end,
                    "kind": "cta",
                    "description": "Closing CTA",
                    "visibleText": ["TRY IT"],
                    "spokenText": [],
                },
            ],
            "candidateClips": [[start + 1, min(end, start + 6)]],
            "landingImplications": ["Lead with the sequence, not the blank opening"],
        },
    }


class Client:
    def __init__(self, outputs, states=("ACTIVE",), fail_upload=False):
        self.outputs = iter(outputs)
        self.states = iter(states)
        self.deleted = []
        self.requests = []
        self.files = NS(
            upload=self.upload,
            get=lambda **_: self.file(),
            delete=lambda **kw: self.deleted.append(kw["name"]),
        )
        self.models = NS(generate_content=self.generate)
        self.fail_upload = fail_upload

    def file(self):
        return NS(
            name="files/test",
            uri="https://example.test/video",
            mime_type="video/mp4",
            state=NS(name=next(self.states, "ACTIVE")),
        )

    def upload(self, **_):
        if self.fail_upload:
            raise RuntimeError("unavailable")
        return self.file()

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        item = next(self.outputs)
        if isinstance(item, Exception):
            raise item
        return NS(text=json.dumps(item))


@pytest.fixture
def source(settings, monkeypatch):
    path = settings.data_dir / "sample.mp4"
    path.write_bytes(b"original video")
    frame = settings.data_dir / "frame.jpg"
    frame.write_bytes(b"sampled frame")
    monkeypatch.setattr(video, "probe_video", lambda _: (10.0, True))
    monkeypatch.setattr(
        video,
        "storyboard",
        lambda *args: [{"timestamp": 6.0, "path": str(frame), "nonblank": True}],
    )
    return path


def test_video_wins_over_thumbnail_and_changed_bytes_invalidate(settings, source):
    thumb = source.with_suffix(".jpg")
    thumb.write_bytes(b"unchanged thumbnail")
    creative = RankedCreative(
        creativeId="ad_1",
        adName="video",
        reasonSelected="selected",
        imagePath=str(thumb),
        videoPath=str(source),
    )
    calls = []

    def model(_, asset):
        calls.append(asset)
        data = observation()
        data["videoEvidence"].update(duration=10, method="video_audio", audioAvailable=True)
        return CreativeAnalysis.model_validate(data)

    analyze_creative(creative, settings, call_model=model)
    analyze_creative(creative, settings, call_model=model)
    source.write_bytes(b"new video bytes")
    analyze_creative(creative, settings, call_model=model)
    assert calls == [source, source]


def test_native_uses_files_audio_two_fps_and_cleans_up(settings, source):
    client = Client([observation()], states=("PROCESSING", "ACTIVE"))
    analysis = video.analyze_video(source, settings, client=client, sleep=lambda _: None)
    evidence = analysis.video_evidence
    assert evidence.method == "video_audio" and evidence.audio_available
    assert evidence.covered_intervals == [(0, 10)]
    assert evidence.moments[0].spoken_text == ["Make an entire family"]
    assert analysis.visible_text == ["TRY IT"]  # narration never merged into visible text
    assert client.deleted == ["files/test"]
    metadata = client.requests[0]["contents"][0].video_metadata
    assert metadata.fps == 2 and metadata.start_offset == "0.0s"
    assert evidence.storyboard[0]["timestamp"] == 6


def test_long_video_covers_all_intervals_in_original_time(settings, source, monkeypatch):
    monkeypatch.setattr(video, "probe_video", lambda _: (250.0, True))
    chunks = video.intervals(250)
    assert chunks == [(0, 120), (118, 238), (236, 250)]
    merged = dict(
        concept="Whole narrative",
        narrative="All three parts",
        primaryPromise="p",
        audienceIntent="a",
        ctaIntent="c",
        suggestedLandingTheme="t",
    )
    client = Client([*(observation(a, b) for a, b in chunks), merged])
    evidence = video.analyze_video(source, settings, client=client).video_evidence
    assert evidence.covered_intervals == chunks
    assert evidence.narrative == "All three parts"
    assert evidence.moments[-1].end == 250
    assert all(0 <= m.start <= m.end <= 250 for m in evidence.moments)
    assert len(evidence.moments) == 6
    assert client.deleted == ["files/test"]


@pytest.mark.parametrize(
    "bad_field",
    [
        "concept",
        "narrative",
        "primaryPromise",
        "audienceIntent",
        "ctaIntent",
        "suggestedLandingTheme",
        "missing_field",
    ],
)
def test_bad_synthesis_preserves_interval_evidence_and_readable_cache(
    settings, source, monkeypatch, bad_field
):
    monkeypatch.setattr(video, "probe_video", lambda _: (130.0, True))
    chunks = video.intervals(130)
    observations = [observation(start, end) for start, end in chunks]
    for index, item in enumerate(observations):
        item["videoEvidence"].update(concept=f"Original concept {index}", narrative=f"Part {index}")
    merged = {
        "concept": "Merged concept",
        "narrative": "Merged narrative",
        "primaryPromise": "Merged promise",
        "audienceIntent": "Merged audience",
        "ctaIntent": "Merged CTA",
        "suggestedLandingTheme": "Merged theme",
    }
    if bad_field == "missing_field":
        del merged["suggestedLandingTheme"]
    else:
        merged[bad_field] = ["Unexpected structured output"]
    client = Client([*observations, merged])
    creative = RankedCreative(
        creativeId="bad_synthesis",
        adName="video",
        reasonSelected="selected",
        videoPath=str(source),
    )
    result = analyze_creative(
        creative,
        settings,
        call_model=lambda *_: video.analyze_video(source, settings, client=client),
    )
    analysis = result.analysis
    evidence = analysis.video_evidence
    assert evidence.concept == "Original concept 0 / Original concept 1"
    assert evidence.narrative == "0–120s: Part 0\n118–130s: Part 1"
    assert evidence.method == "video_audio" and evidence.audio_available
    assert evidence.covered_intervals == chunks and len(evidence.moments) == 4
    for field in ("primaryPromise", "audienceIntent", "ctaIntent", "suggestedLandingTheme"):
        assert analysis.model_dump(by_alias=True)[field] == observations[0][field]
    assert any("synthesis unavailable" in warning for warning in evidence.limitations)
    cached = analyze_creative(
        creative, settings, call_model=lambda *_: pytest.fail("Usable evidence should be cached")
    )
    assert cached == result


@pytest.mark.parametrize("provider_enabled", [False, True])
def test_decoder_timeout_preserves_title_body_fallback(
    settings, source, monkeypatch, provider_enabled
):
    def timed_out(_):
        raise subprocess.TimeoutExpired("ffprobe", 90)

    monkeypatch.setattr(video, "probe_video", timed_out)
    creative = RankedCreative(
        creativeId="decoder_timeout",
        adName="video",
        reasonSelected="selected",
        title="Create consistent icons",
        videoPath=str(source),
    )
    model = (lambda *_: timed_out(source)) if provider_enabled else False
    result = analyze_creative(creative, settings, call_model=model)
    assert result.model == "title-body-fallback"
    assert result.analysis.primary_promise == creative.title
    assert result.analysis.video_evidence is None
    assert analyze_creative(creative, settings, call_model=False) == result


def test_silent_video_does_not_claim_narration(settings, source, monkeypatch):
    monkeypatch.setattr(video, "probe_video", lambda _: (10.0, False))
    evidence = video.analyze_video(source, settings, client=Client([observation()])).video_evidence
    assert not evidence.audio_available and all(not m.spoken_text for m in evidence.moments)


@pytest.mark.parametrize("failure", ["upload", "model", "timeout"])
def test_native_failure_preserves_truthful_visual_fallback(settings, source, failure):
    outputs = (
        [RuntimeError("provider failure"), observation()] if failure == "model" else [observation()]
    )
    client = Client(
        outputs,
        states=("PROCESSING",) if failure == "timeout" else ("ACTIVE",),
        fail_upload=failure == "upload",
    )
    clock = iter([0, 181])
    evidence = video.analyze_video(
        source, settings, client=client, clock=lambda: next(clock), sleep=lambda _: None
    ).video_evidence
    assert evidence.method == "visual_only" and not evidence.audio_available
    assert evidence.covered_intervals == [] and evidence.candidate_clips == []
    assert all(not m.spoken_text for m in evidence.moments)
    assert any("narration unavailable" in text for text in evidence.limitations)
    assert client.deleted == ([] if failure == "upload" else ["files/test"])
    assert source.exists()


def test_total_provider_outage_keeps_storyboard_and_retry_evidence(settings, source):
    client = Client([RuntimeError("no credits")], fail_upload=True)
    evidence = video.analyze_video(source, settings, client=client).video_evidence
    assert evidence.method == "visual_only" and not evidence.moments
    assert evidence.storyboard and "unavailable" in evidence.concept
    assert any("Reanalyze" in text for text in evidence.limitations)


def test_bad_chunk_timestamps_use_visual_fallback(settings, source):
    bad = observation()
    bad["videoEvidence"]["moments"][0]["start"] = 100
    client = Client([bad, observation()])
    assert (
        video.analyze_video(source, settings, client=client).video_evidence.method == "visual_only"
    )


@pytest.mark.parametrize("duration", [0.04, 1.0, 120.0, 120.1, 360.0])
def test_intervals_never_skip_the_end(duration):
    chunks = video.intervals(duration)
    assert chunks[0][0] == 0 and chunks[-1][1] == duration
    assert all(0 <= a < b <= duration and b - a <= 120 for a, b in chunks)
    assert all(right[0] == left[1] - 2 for left, right in zip(chunks, chunks[1:]))


def test_storyboard_blank_opening_portrait_and_final_cta(tmp_path):
    path = tmp_path / "portrait.mp4"
    video.run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=120x200:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=120x200:duration=2:rate=10",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    duration, audio = video.probe_video(path)
    frames = video.storyboard(path, duration, tmp_path / "frames")
    assert len(frames) <= 12 and frames[-1]["timestamp"] > 2.8
    assert not frames[0]["nonblank"] and any(f["nonblank"] for f in frames[1:])
    assert not audio and path.exists()


def test_storyboard_keeps_closing_cta_among_many_fast_cuts(source, settings, monkeypatch):
    from funnel_growth_agent.models import VideoMoment

    monkeypatch.undo()
    observed = []

    def frame(path, timestamp, dest):
        observed.append(timestamp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"image")

    monkeypatch.setattr(video, "extract_frame", frame)
    monkeypatch.setattr(video, "run_media", lambda args, **kw: bytes([0, 255]) * 128)
    moments = [
        VideoMoment(start=i, end=i + 0.2, kind="scene", description="Cut") for i in range(25)
    ]
    moments += [VideoMoment(start=28, end=29, kind="cta", description="Final call to action")]
    frames = video.storyboard(source, 30, settings.analysis_dir / "fast", moments)
    assert len(frames) <= 12 and 28 in observed and max(observed) >= 29.8


def test_narration_only_and_changing_subtitles_remain_separate(settings, source):
    data = observation()
    data["visibleText"] = []
    data["videoEvidence"]["moments"] = [
        {
            "start": 0,
            "end": 2,
            "kind": "hook",
            "description": "An unlabelled object",
            "visibleText": [],
            "spokenText": ["Make hundreds of variations"],
        },
        {
            "start": 2,
            "end": 4,
            "kind": "demonstration",
            "description": "Subtitle changes",
            "visibleText": ["BEFORE"],
            "spokenText": [],
        },
        {
            "start": 4,
            "end": 6,
            "kind": "payoff",
            "description": "New result appears",
            "visibleText": ["AFTER"],
            "spokenText": [],
        },
    ]
    result = video.analyze_video(source, settings, client=Client([data]))
    assert result.visible_text == []
    assert result.video_evidence.moments[0].spoken_text == ["Make hundreds of variations"]
    assert [m.visible_text for m in result.video_evidence.moments] == [[], ["BEFORE"], ["AFTER"]]


def test_flattened_provider_reply_keeps_its_video_evidence(settings, source):
    """The reply sometimes arrives with the two levels merged; the evidence still counts."""
    data = observation()
    flat = {k: v for k, v in data.items() if k != "videoEvidence"} | data["videoEvidence"]
    analysis = video.analyze_video(source, settings, client=Client([flat]))
    evidence = analysis.video_evidence
    assert evidence.method == "video_audio"
    assert evidence.concept == "One design becomes a consistent set"
    assert [m.kind for m in evidence.moments] == ["demonstration", "cta"]
    assert analysis.primary_promise == "Create consistent icons"


def test_wrapped_provider_reply_keeps_its_video_evidence(settings, source):
    client = Client([{"creativeAnalysis": observation()}])
    analysis = video.analyze_video(source, settings, client=client)
    assert analysis.video_evidence.concept == "One design becomes a consistent set"
    assert analysis.visible_text == ["TRY IT"]


def test_prose_lists_do_not_discard_the_analysis(settings, source):
    data = observation()
    data["videoEvidence"]["landingImplications"] = "Lead with the sequence\nShow the export"
    data["videoEvidence"]["moments"][0]["visibleText"] = "EDIT"
    analysis = video.analyze_video(source, settings, client=Client([data]))
    evidence = analysis.video_evidence
    assert evidence.landing_implications == ["Lead with the sequence", "Show the export"]
    assert evidence.moments[0].visible_text == ["EDIT"]


def test_unusable_fallback_reply_keeps_the_storyboard(settings, source):
    """A visual-only reply that misses the schema must not cost the whole creative read."""
    client = Client([RuntimeError("native unavailable"), {"unexpected": "shape"}])
    analysis = video.analyze_video(source, settings, client=client)
    evidence = analysis.video_evidence
    assert evidence.method == "visual_only" and evidence.storyboard
    assert "unavailable" in evidence.concept and not evidence.moments
    assert any("did not match the analysis schema" in text for text in evidence.limitations)
    assert any("Reanalyze" in text for text in evidence.limitations)
