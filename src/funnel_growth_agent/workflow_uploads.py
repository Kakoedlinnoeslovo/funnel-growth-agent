"""Persistent local creative uploads and decoded image references."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from .config import Settings
from .landing_diff import ApplyError
from .media import MediaTools, _run, default_media_tools
from .workflow_catalog import METRICS

IMAGE_LIMIT = 20 * 1024 * 1024
VIDEO_LIMIT = 100 * 1024 * 1024
UPLOAD_TYPES = {
    ".jpg": ("image", {"mjpeg"}),
    ".jpeg": ("image", {"mjpeg"}),
    ".png": ("image", {"png"}),
    ".webp": ("image", {"webp"}),
    ".mp4": ("video", None),
    ".mov": ("video", None),
    ".webm": ("video", None),
}
INPUT_OPTIONS = [
    "-protocol_whitelist",
    "file,pipe",
    "-format_whitelist",
    "image2,jpeg_pipe,png_pipe,webp_pipe,mov,matroska,webm",
]


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def upload_catalog(settings: Settings) -> list[dict]:
    rows = []
    for manifest in sorted((settings.data_dir / "uploads").glob("upload_*/creative.json")):
        folder = manifest.parent
        if not re.fullmatch(r"upload_[a-f0-9]{32}", folder.name):
            continue
        data = json.loads(manifest.read_text())
        suffix = data["suffix"]
        if suffix not in UPLOAD_TYPES:
            continue
        original = folder / ("original" + suffix)
        image = folder / "image.jpg"
        if not original.is_file() or not image.is_file():
            continue
        video = UPLOAD_TYPES[suffix][0] == "video"
        rows.append(
            {
                "id": folder.name,
                "source": "upload",
                "name": data["name"],
                "adsetId": None,
                "adsetName": "",
                "campaignName": "",
                "title": "",
                "body": "",
                "linkUrl": None,
                "metrics": dict.fromkeys(METRICS),
                "imagePath": str(image),
                "videoPath": str(original) if video else None,
                "warnings": [],
                "signal": "Uploaded · No performance data",
                "sufficient": False,
                "frameSource": "First video frame" if video else None,
                "duration": data.get("duration"),
                "createdAt": data["createdAt"],
            }
        )
    return rows


def save_upload(
    settings: Settings,
    stream: BinaryIO,
    *,
    filename: str,
    length: int,
    tools: MediaTools | None = None,
) -> dict:
    # The browser's name is display-only; it can never choose a filesystem path.
    name = filename.replace("\\", "/").split("/")[-1]
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise UploadError("Choose a file with a valid name.")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise UploadError("Choose a JPEG, PNG, WebP, MP4, MOV, or WebM creative.", 415)
    kind, codecs = UPLOAD_TYPES[suffix]
    limit = IMAGE_LIMIT if kind == "image" else VIDEO_LIMIT
    if length <= 0:
        raise UploadError("The uploaded file is empty.")
    if length > limit:
        raise UploadError(f"This {kind} exceeds the {limit // (1024 * 1024)} MiB limit.", 413)
    tools = tools or default_media_tools(settings)
    root = settings.data_dir / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    creative_id = "upload_" + uuid.uuid4().hex
    with TemporaryDirectory(prefix=".upload-", dir=root) as temporary:
        folder = Path(temporary)
        original = folder / ("original" + suffix)
        try:
            with original.open("wb") as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise UploadError("Upload was interrupted. Choose the file again.")
                    target.write(chunk)
                    remaining -= len(chunk)
            result = _run(
                tools,
                [
                    tools.ffprobe,
                    "-v",
                    "error",
                    *INPUT_OPTIONS,
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(original),
                ],
                timeout=30,
                what="read uploaded creative",
            )
            probe = json.loads(result.stdout)
            video = next(
                (
                    s
                    for s in probe.get("streams", [])
                    if s.get("codec_type") == "video"
                    and (kind == "image" or not s.get("disposition", {}).get("attached_pic"))
                ),
                None,
            )
            if not video or not video.get("width") or not video.get("height"):
                raise UploadError("This file has no readable image or video frames.")
            if video["width"] * video["height"] > 40_000_000:
                raise UploadError("Creative frames must be no larger than 40 megapixels.")
            if codecs and video.get("codec_name") not in codecs:
                raise UploadError("The file contents do not match its image extension.", 415)
            formats = set(probe.get("format", {}).get("format_name", "").split(","))
            expected = {"matroska", "webm"} if suffix == ".webm" else {"mov", "mp4"}
            if kind == "video" and not formats.intersection(expected):
                raise UploadError("The file contents do not match its video extension.", 415)
            image = folder / "image.jpg"
            _run(
                tools,
                [
                    tools.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    *INPUT_OPTIONS,
                    "-i",
                    str(original),
                    "-map",
                    f"0:{video.get('index', 0)}",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(2048,iw)':'min(2048,ih)':force_original_aspect_ratio=decrease",
                    "-q:v",
                    "2",
                    "-update",
                    "1",
                    str(image),
                ],
                timeout=60,
                what="extract creative image",
            )
            if not image.is_file() or image.stat().st_size == 0:
                raise UploadError("Could not decode an image from this creative.")
        except ApplyError as error:
            missing = "is not installed" in str(error)
            raise UploadError(
                "Creative processing needs FFmpeg and FFprobe installed."
                if missing
                else "Could not read this creative. Try another image or video file.",
                503 if missing else 400,
            ) from error
        except (OSError, TimeoutError) as error:
            raise UploadError("Upload was interrupted. Choose the file again.") from error
        data = {
            "name": name,
            "suffix": suffix,
            "duration": probe.get("format", {}).get("duration") if kind == "video" else None,
            "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        (folder / "creative.json").write_text(json.dumps(data))
        os.replace(folder, root / creative_id)
    return next(row for row in upload_catalog(settings) if row["id"] == creative_id)
