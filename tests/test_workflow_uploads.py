from __future__ import annotations

import http.client
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from funnel_growth_agent.media import MediaTools, SubprocessRunner
from funnel_growth_agent.workflow_uploads import (
    IMAGE_LIMIT,
    VIDEO_LIMIT,
    UploadError,
    save_upload,
    upload_catalog,
)


@pytest.fixture
def media_file(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real media decoding requires ffmpeg and ffprobe")

    def create(suffix, color="red"):
        path = tmp_path / ("source" + suffix)
        if suffix == ".webp":
            if not shutil.which("cwebp"):
                pytest.skip("WebP fixture requires cwebp")
            png = create(".png", color)
            subprocess.run(
                ["cwebp", "-quiet", str(png), "-o", str(path)],
                check=True,
                capture_output=True,
                timeout=20,
            )
            return path
        options = ["-frames:v", "1"] if suffix in {".jpg", ".png", ".webp"} else ["-t", "0.3"]
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=64x48:r=10",
                *options,
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        return path

    return create


@pytest.mark.parametrize("suffix", [".jpg", ".png", ".webp", ".mp4", ".mov", ".webm"])
def test_upload_decodes_supported_formats_and_survives_reload(settings, media_file, suffix):
    source = media_file(suffix)
    data = source.read_bytes()
    row = save_upload(
        settings, io.BytesIO(data), filename="../../creative" + suffix, length=len(data)
    )
    assert row["name"] == "creative" + suffix
    assert row["id"].startswith("upload_") and len(row["id"]) == 39
    assert all(value is None for value in row["metrics"].values())
    image = Path(row["imagePath"])
    assert image.read_bytes().startswith(b"\xff\xd8")
    assert image.is_relative_to(settings.data_dir / "uploads")
    assert not list((settings.data_dir / "uploads").glob(".upload-*"))
    assert upload_catalog(settings) == [row]
    if suffix in {".mp4", ".mov", ".webm"}:
        assert Path(row["videoPath"]).read_bytes() == data
        assert row["frameSource"] == "First video frame"
        assert float(row["duration"]) > 0
    else:
        assert row["videoPath"] is None


def test_black_first_frame_is_accepted_as_evidence(settings, media_file):
    source = media_file(".mp4", color="black")
    with source.open("rb") as stream:
        row = save_upload(settings, stream, filename="blank.mp4", length=source.stat().st_size)
    rgb = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            row["imagePath"],
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout
    assert max(rgb) < 5  # We retain the actual opening frame rather than inventing a replacement.


def test_rotated_video_frame_keeps_display_orientation(settings, media_file, tmp_path):
    source = media_file(".mp4")
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-display_rotation",
            "90",
            "-i",
            str(source),
            "-c",
            "copy",
            str(rotated),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    with rotated.open("rb") as stream:
        row = save_upload(settings, stream, filename="rotated.mp4", length=rotated.stat().st_size)
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            row["imagePath"],
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    stream = json.loads(output.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (48, 64)


def test_short_uploaded_video_is_only_a_frame_reference(settings, media_file):
    from funnel_growth_agent.media import creative_source
    from funnel_growth_agent.sources import media_sources
    from funnel_growth_agent.workflow_catalog import ranked_selection

    data = media_file(".mp4").read_bytes()
    row = save_upload(settings, io.BytesIO(data), filename="short.mp4", length=len(data))
    ranked = ranked_selection([row])
    assert ranked[0].video_duration < 4
    assert media_sources(settings, ranked)["creatives"] == []
    settings.creative_paths = {row["id"]: {"video": row["videoPath"], "image": row["imagePath"]}}
    assert creative_source(row["id"], settings) == Path(row["videoPath"])


@pytest.mark.parametrize(
    "filename,length,status",
    [
        ("file.svg", 4, 415),
        ("image.png", IMAGE_LIMIT + 1, 413),
        ("video.mp4", VIDEO_LIMIT + 1, 413),
        ("empty.png", 0, 400),
        ("bad\n.png", 4, 400),
    ],
)
def test_invalid_upload_headers_rejected_before_reading(settings, filename, length, status):
    class Unreadable:
        def read(self, _):
            pytest.fail("Invalid request body must not be read")

    with pytest.raises(UploadError) as exc:
        save_upload(settings, Unreadable(), filename=filename, length=length)
    assert exc.value.status == status
    assert upload_catalog(settings) == []


def test_interrupted_upload_removes_partial_files(settings):
    with pytest.raises(UploadError, match="interrupted"):
        save_upload(settings, io.BytesIO(b"short"), filename="file.png", length=20)
    assert not list((settings.data_dir / "uploads").iterdir())


def test_missing_media_tool_is_actionable_and_removes_partial_files(settings):
    tools = MediaTools(runner=SubprocessRunner(), ffprobe="/no/such/ffprobe")
    with pytest.raises(UploadError, match="FFmpeg and FFprobe") as exc:
        save_upload(settings, io.BytesIO(b"data"), filename="file.png", length=4, tools=tools)
    assert exc.value.status == 503
    assert not list((settings.data_dir / "uploads").iterdir())


def test_corrupt_and_mislabeled_media_are_not_registered(settings, media_file):
    with pytest.raises(UploadError):
        save_upload(settings, io.BytesIO(b"not a png"), filename="bad.png", length=9)
    data = media_file(".mp4").read_bytes()
    with pytest.raises(UploadError, match="contents do not match"):
        save_upload(settings, io.BytesIO(data), filename="fake.png", length=len(data))
    assert not list((settings.data_dir / "uploads").iterdir())


def test_frame_reused_for_analysis_and_cta_text_distinguished(settings, media_file):
    from funnel_growth_agent.gemini import analyze_creative
    from funnel_growth_agent.models import CreativeAnalysis
    from funnel_growth_agent.workflow_catalog import ranked_selection

    data = media_file(".mp4").read_bytes()
    row = save_upload(settings, io.BytesIO(data), filename="video.mp4", length=len(data))
    calls = []

    def analyze(creative, asset):
        calls.append(asset)
        assert asset == Path(row["imagePath"])
        return CreativeAnalysis(
            visualHook="Opening frame",
            primaryPromise="Editable vectors",
            audienceIntent="Designers",
            ctaIntent="Try vector editing",
            visibleText=["TRY NOW"],
            suggestedLandingTheme="Vector tools",
        )

    creative = ranked_selection([row])[0]
    first = analyze_creative(creative, settings, call_model=analyze)
    second = analyze_creative(creative, settings, call_model=analyze)
    assert first == second and len(calls) == 1
    assert first.analysis.visible_text == ["TRY NOW"]
    assert first.analysis.cta_intent == "Try vector editing"


def test_large_decoded_dimensions_rejected_before_frame_extraction(settings):
    class Runner:
        def run(self, argv, **_):
            assert Path(argv[0]).name == "ffprobe"
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "streams": [{"codec_type": "video", "width": 10000, "height": 10000}],
                    }
                ),
                "",
            )

    with pytest.raises(UploadError, match="40 megapixels"):
        save_upload(
            settings,
            io.BytesIO(b"data"),
            filename="large.png",
            length=4,
            tools=MediaTools(runner=Runner()),
        )
    assert not list((settings.data_dir / "uploads").iterdir())


def test_upload_http_route_origin_limits_catalog_and_assets(settings, media_file):
    from funnel_growth_agent.demo.server import make_server

    data = media_file(".png").read_bytes()
    server = make_server(
        settings, recording=None, live=True, port=0, preview_base="http://127.0.0.1:9/pm"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        code, payload = response.status, response.read()
        conn.close()
        return code, payload

    try:
        headers = {"Content-Type": "image/png", "X-File-Name": "folder%2Fmy%20creative.png"}
        code, _ = request(
            "POST", "/api/uploads", data, {**headers, "Origin": "https://other.example"}
        )
        assert code == 403 and upload_catalog(settings) == []
        code, body = request("POST", "/api/uploads", data, headers)
        row = json.loads(body)
        assert code == 201, body
        assert row["name"] == "my creative.png" and "imagePath" not in row
        code, image = request("GET", row["imageUrl"])
        assert code == 200 and image.startswith(b"\xff\xd8")
        code, body = request("GET", "/api/catalog")
        assert row["id"] in {item["id"] for item in json.loads(body)["creatives"]}
        code, _ = request(
            "POST", "/api/uploads", b"", {**headers, "Content-Length": str(IMAGE_LIMIT + 1)}
        )
        assert code == 413
        code, _ = request("POST", "/api/uploads", b"nope", {**headers, "X-File-Name": "file.svg"})
        assert code == 415
        assert len(upload_catalog(settings)) == 1
    finally:
        server.shutdown()
        server.server_close()
