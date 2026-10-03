from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import subprocess
from pathlib import Path

from yt_dlp.utils import ISO639Utils

LOGGER = logging.getLogger(__name__)

SUBTITLE_EXTS = (".vtt", ".srt", ".ass", ".ssa")

# Контейнеры, которые клиенты открывают напрямую.
COMPATIBLE_CONTAINERS = frozenset({"mp4", "mov"})
COMPATIBLE_VIDEO_CODECS = frozenset({"h264", "hevc"})

# Только AAC. ac3/eac3/mp3 формально играются частью плееров, но на практике
# именно eac3-дорожки с YouTube (itag 328, Dolby Digital Plus 5.1) давали
# немое видео на tvOS, поэтому в белом списке их больше нет.
COMPATIBLE_AUDIO_CODECS = frozenset({"aac"})

# Многоканальный звук приводим к стерео: 5.1 в MP4 приезжает с
# channel_layout=unknown, и AVPlayer на таком файле молчит.
MAX_COMPATIBLE_AUDIO_CHANNELS = 2


@dataclass(slots=True)
class FFmpegTranscodeOptions:
    preset: str = "superfast"
    crf: int = 24
    audio_bitrate: str = "128k"
    threads: int = 1


@dataclass(slots=True, frozen=True)
class AudioStreamInfo:
    codec: str
    channels: int


@dataclass(slots=True, frozen=True)
class MediaInfo:
    container_formats: frozenset[str]
    video_codecs: frozenset[str]
    audio_streams: tuple[AudioStreamInfo, ...]
    duration_seconds: float | None

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)

    @property
    def has_video(self) -> bool:
        return bool(self.video_codecs)


class MediaProbeError(RuntimeError):
    """ffprobe не смог прочитать файл."""


def probe_media(path: Path) -> MediaInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:stream=codec_type,codec_name,channels",
        "-of",
        "json",
        str(path),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MediaProbeError(f"ffprobe failed for {path}: {result.stderr.strip()}")

    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MediaProbeError(f"ffprobe returned invalid JSON for {path}") from error

    container_formats = frozenset(
        item.strip().lower()
        for item in str(probe.get("format", {}).get("format_name", "")).split(",")
        if item.strip()
    )

    streams = probe.get("streams", [])
    video_codecs = frozenset(
        str(stream.get("codec_name", "")).lower()
        for stream in streams
        if stream.get("codec_type") == "video"
    )
    audio_streams = tuple(
        AudioStreamInfo(
            codec=str(stream.get("codec_name", "")).lower(),
            channels=int(stream.get("channels") or 0),
        )
        for stream in streams
        if stream.get("codec_type") == "audio"
    )

    return MediaInfo(
        container_formats=container_formats,
        video_codecs=video_codecs,
        audio_streams=audio_streams,
        duration_seconds=parse_duration(probe.get("format", {}).get("duration")),
    )


def parse_duration(raw_value: object) -> float | None:
    try:
        duration = float(str(raw_value))
    except (TypeError, ValueError):
        return None

    return duration if duration > 0 else None


def needs_video_transcode(info: MediaInfo) -> bool:
    if not info.container_formats & COMPATIBLE_CONTAINERS:
        return True

    return not bool(info.video_codecs & COMPATIBLE_VIDEO_CODECS)


def needs_audio_transcode(info: MediaInfo) -> bool:
    if not info.has_audio:
        return False

    # Играется первая дорожка, её и проверяем.
    stream = info.audio_streams[0]
    if stream.codec not in COMPATIBLE_AUDIO_CODECS:
        return True

    return stream.channels > MAX_COMPATIBLE_AUDIO_CHANNELS


def convert_to_mp4(
    source_path: Path,
    target_path: Path,
    *,
    options: FFmpegTranscodeOptions | None = None,
    subtitle_files: list[Path] | None = None,
    transcode_video: bool = True,
    transcode_audio: bool = True,
    has_audio: bool = True,
) -> Path:
    options = options or FFmpegTranscodeOptions()
    subtitle_files = subtitle_files or []
    temp_target_path = (
        target_path.with_name(f"{target_path.stem}.transcoding{target_path.suffix}")
        if source_path == target_path
        else target_path
    )

    command: list[str] = ["ffmpeg", "-y", "-i", str(source_path)]
    for sub in subtitle_files:
        command += ["-i", str(sub)]

    command += ["-map", "0:v:0"]
    if has_audio:
        # Без "?": если звук ожидается, но его нет, ffmpeg должен упасть,
        # а не молча собрать немой файл.
        command += ["-map", "0:a:0"]
    for index in range(1, len(subtitle_files) + 1):
        command += ["-map", f"{index}:0"]

    if transcode_video:
        command += [
            "-c:v", "libx264",
            "-preset", options.preset,
            "-crf", str(options.crf),
            "-pix_fmt", "yuv420p",
            "-threads", str(options.threads),
        ]
    else:
        command += ["-c:v", "copy"]

    if has_audio:
        if transcode_audio:
            command += [
                "-c:a", "aac",
                "-b:a", options.audio_bitrate,
                "-ac", str(MAX_COMPATIBLE_AUDIO_CHANNELS),
            ]
        else:
            command += ["-c:a", "copy"]

    if subtitle_files:
        command += ["-c:s", "mov_text"]
        for stream_index, sub in enumerate(subtitle_files):
            lang_code = subtitle_language_code(sub)
            if lang_code:
                command += [f"-metadata:s:s:{stream_index}", f"language={lang_code}"]

    command += ["-movflags", "+faststart", str(temp_target_path)]

    LOGGER.info("Running ffmpeg: %s", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        temp_target_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.strip()}")

    if temp_target_path != target_path:
        temp_target_path.replace(target_path)
    elif source_path != target_path:
        source_path.unlink(missing_ok=True)

    if source_path != target_path:
        source_path.unlink(missing_ok=True)

    for sub in subtitle_files:
        sub.unlink(missing_ok=True)

    return target_path


def find_subtitle_files(video_path: Path) -> list[Path]:
    stem = video_path.stem
    candidates = (
        path
        for path in video_path.parent.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUBTITLE_EXTS
        and path.stem.split(".", 1)[0] == stem
    )
    return sorted(candidates)


def subtitle_language_code(sub_path: Path) -> str | None:
    parts = sub_path.name.split(".")
    if len(parts) < 3:
        return None
    short = parts[-2]
    return ISO639Utils.short2long(short) or short


def is_apple_tv_compatible(path: Path) -> bool:
    try:
        info = probe_media(path)
    except MediaProbeError:
        return False

    if needs_video_transcode(info):
        return False

    return not needs_audio_transcode(info)
