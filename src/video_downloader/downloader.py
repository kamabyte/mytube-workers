from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from yt_dlp.utils import DownloadError

from video_downloader.domain.models import DownloadJob, DownloadOutcome
from video_downloader.infrastructure.converters.ffmpeg import (
    FFmpegTranscodeOptions,
    MediaProbeError,
    SUBTITLE_EXTS,
    convert_to_mp4,
    find_subtitle_files,
    needs_audio_transcode,
    needs_video_transcode,
    probe_media,
)
from video_downloader.infrastructure.downloaders.yt_dlp import (
    download_video_with_yt_dlp,
    is_range_not_satisfiable_error,
)

LOGGER = logging.getLogger(__name__)

# Контейнеры, которые yt-dlp может оставить после себя как результат загрузки.
# Всё остальное (.part, .ytdl, .vtt) — служебное и за готовый файл не считается.
DOWNLOAD_EXTS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts", ".flv"})

# Промежуточные имена: "2859.f137.mp4" — отдельная дорожка до слияния,
# "2859.temp.mp4" — результат слияния до переименования, "2859.transcoding.mp4" —
# наш собственный ffmpeg. Подхватить любое из них за готовую загрузку нельзя.
FORMAT_FRAGMENT_PATTERN = re.compile(r"\.f\d+$")
INTERMEDIATE_STEM_SUFFIXES = (".temp", ".transcoding")

# Длительность в БД приходит от YouTube и на секунду-другую расходится с
# контейнером, поэтому сверяем с допуском.
DURATION_TOLERANCE_RATIO = 0.02
DURATION_TOLERANCE_SECONDS = 5.0


class MediaValidationError(RuntimeError):
    """Файл скачался, но результат непригоден: нет звука или он обрезан."""


def download_video(
    video: DownloadJob,
    download_dir: Path,
    archive_file: Path,
    *,
    transcode_for_apple_tv: bool = False,
    transcode_options: FFmpegTranscodeOptions | None = None,
    yt_dlp_cookie_file: Path | None = None,
    yt_dlp_cookies_from_browser: tuple[str, ...] | None = None,
    yt_dlp_node_path: Path | None = None,
    subtitle_langs: tuple[str, ...] = (),
) -> DownloadOutcome:
    video_dir = download_dir / str(video.channel_id)
    video_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(video_dir / f"{video.id}.%(ext)s")
    existing_path = find_existing_download(video_dir, video.id, video.external_id)

    if existing_path is not None:
        return finalize_validated(
            existing_path,
            video,
            video_dir,
            archive_file,
            transcode_for_apple_tv=transcode_for_apple_tv,
            transcode_options=transcode_options,
        )

    if is_video_in_archive(archive_file, video.external_id):
        LOGGER.info("Archive entry is stale for video id=%s external_id=%s. Retrying download.", video.id, video.external_id)
        remove_video_from_archive(archive_file, video.external_id)

    # Готового файла нет, значит всё, что лежит рядом, — хвосты прошлой
    # оборванной попытки. Чистим, чтобы yt-dlp не дописывал их поверх.
    cleanup_stale_download_artifacts(video_dir, video.id, video.external_id)

    download_started = time.monotonic()
    try:
        download_video_with_yt_dlp(
            video,
            output_template,
            archive_file,
            yt_dlp_cookie_file=yt_dlp_cookie_file,
            yt_dlp_cookies_from_browser=yt_dlp_cookies_from_browser,
            yt_dlp_node_path=yt_dlp_node_path,
            subtitle_langs=subtitle_langs,
        )
    except DownloadError as error:
        if not is_range_not_satisfiable_error(error):
            raise

        LOGGER.warning(
            "yt-dlp returned HTTP 416 for id=%s external_id=%s. "
            "Removing stale download artifacts and retrying once from scratch.",
            video.id,
            video.external_id,
        )
        cleanup_stale_download_artifacts(video_dir, video.id, video.external_id)
        download_video_with_yt_dlp(
            video,
            output_template,
            archive_file,
            yt_dlp_cookie_file=yt_dlp_cookie_file,
            yt_dlp_cookies_from_browser=yt_dlp_cookies_from_browser,
            yt_dlp_node_path=yt_dlp_node_path,
            subtitle_langs=subtitle_langs,
        )

    download_seconds = time.monotonic() - download_started

    downloaded_path = find_existing_download(video_dir, video.id, video.external_id)
    if downloaded_path is not None:
        outcome = finalize_validated(
            downloaded_path,
            video,
            video_dir,
            archive_file,
            transcode_for_apple_tv=transcode_for_apple_tv,
            transcode_options=transcode_options,
        )
        outcome.download_seconds = download_seconds
        return outcome

    raise FileNotFoundError(f"Downloaded file for video {video.id} was not found in {video_dir}")


def finalize_validated(
    path: Path,
    video: DownloadJob,
    video_dir: Path,
    archive_file: Path,
    *,
    transcode_for_apple_tv: bool,
    transcode_options: FFmpegTranscodeOptions | None = None,
) -> DownloadOutcome:
    """finalize_download плюс уборка за собой, если результат не прошёл проверку.

    Битый файл обязательно убрать: иначе find_existing_download найдёт его на
    следующем цикле, сочтёт готовой загрузкой и задача застрянет навсегда.
    """
    try:
        return finalize_download(
            path,
            video_dir,
            video.id,
            transcode_for_apple_tv=transcode_for_apple_tv,
            transcode_options=transcode_options,
            expected_duration_seconds=video.duration_seconds,
        )
    except MediaValidationError:
        LOGGER.error(
            "Discarding unusable download for id=%s external_id=%s. It will be fetched again next cycle.",
            video.id,
            video.external_id,
        )
        cleanup_stale_download_artifacts(video_dir, video.id, video.external_id)
        remove_video_from_archive(archive_file, video.external_id)
        raise


def finalize_download(
    path: Path,
    video_dir: Path,
    video_id: int,
    *,
    transcode_for_apple_tv: bool,
    transcode_options: FFmpegTranscodeOptions | None = None,
    expected_duration_seconds: int | None = None,
) -> DownloadOutcome:
    normalized_path = normalize_download_path(path, video_dir, video_id)
    subtitle_files = normalize_subtitle_paths(path, normalized_path)

    # Сверяем то, что реально скачалось, до любых перекодировок: обрезанный
    # или немой исходник дальше по конвейеру превратится в такой же битый mp4.
    validate_media(normalized_path, video_id=video_id, expected_duration_seconds=expected_duration_seconds)

    if not transcode_for_apple_tv:
        if not subtitle_files:
            return DownloadOutcome(path=normalized_path)

        LOGGER.info(
            "Embedding subtitles into MP4 without Apple TV transcoding. id=%s path=%s subtitles=%d",
            video_id,
            normalized_path,
            len(subtitle_files),
        )
        started = time.monotonic()
        result_path = convert_to_mp4(
            normalized_path,
            video_dir / f"{video_id}.mp4",
            options=transcode_options,
            subtitle_files=subtitle_files,
            transcode_video=False,
            transcode_audio=False,
        )
        return DownloadOutcome(path=result_path, transcode_seconds=time.monotonic() - started)

    return ensure_apple_tv_compatible(normalized_path, video_dir, video_id, transcode_options=transcode_options)


def ensure_apple_tv_compatible(
    path: Path,
    video_dir: Path,
    video_id: int,
    *,
    transcode_options: FFmpegTranscodeOptions | None = None,
) -> DownloadOutcome:
    subtitle_files = find_subtitle_files(path)

    try:
        info = probe_media(path)
    except MediaProbeError as error:
        raise MediaValidationError(f"Video {video_id}: ffprobe could not read {path}") from error

    transcode_video = needs_video_transcode(info)
    transcode_audio = needs_audio_transcode(info)

    if not transcode_video and not transcode_audio and not subtitle_files:
        return DownloadOutcome(path=path)

    target_path = video_dir / f"{video_id}.mp4"
    LOGGER.info(
        "Running ffmpeg for Apple TV compatibility. id=%s path=%s video=%s audio=%s subtitles=%d",
        video_id,
        path,
        "transcode" if transcode_video else "copy",
        "transcode" if transcode_audio else "copy",
        len(subtitle_files),
    )
    started = time.monotonic()
    result_path = convert_to_mp4(
        path,
        target_path,
        options=transcode_options,
        subtitle_files=subtitle_files,
        transcode_video=transcode_video,
        transcode_audio=transcode_audio,
        has_audio=info.has_audio,
    )
    return DownloadOutcome(path=result_path, transcode_seconds=time.monotonic() - started)


def validate_media(
    path: Path,
    *,
    video_id: int,
    expected_duration_seconds: int | None,
) -> None:
    try:
        info = probe_media(path)
    except MediaProbeError as error:
        raise MediaValidationError(f"Video {video_id}: ffprobe could not read {path}") from error

    if not info.has_video:
        raise MediaValidationError(f"Video {video_id}: downloaded file has no video stream ({path})")

    # Загрузка, оборванная до слияния дорожек, даёт video-only файл. Раньше
    # он молча доезжал до клиента как немое видео.
    if not info.has_audio:
        raise MediaValidationError(f"Video {video_id}: downloaded file has no audio stream ({path})")

    if not expected_duration_seconds or expected_duration_seconds <= 0:
        return

    if info.duration_seconds is None:
        raise MediaValidationError(f"Video {video_id}: could not read duration of {path}")

    tolerance = max(DURATION_TOLERANCE_SECONDS, expected_duration_seconds * DURATION_TOLERANCE_RATIO)
    if abs(info.duration_seconds - expected_duration_seconds) > tolerance:
        raise MediaValidationError(
            f"Video {video_id}: duration mismatch, file is {info.duration_seconds:.0f}s "
            f"but catalog says {expected_duration_seconds}s ({path})"
        )


def find_existing_download(video_dir: Path, video_id: int, external_id: str) -> Path | None:
    candidates = [path for path in sorted(video_dir.glob(f"{video_id}.*")) if is_finished_download(path)]
    if candidates:
        return candidates[0]

    legacy_candidates = [
        path for path in sorted(video_dir.glob(f"{external_id}.*")) if is_finished_download(path)
    ]
    if legacy_candidates:
        return legacy_candidates[0]

    return None


def is_finished_download(path: Path) -> bool:
    """Отличает готовую загрузку от служебных файлов yt-dlp и наших собственных.

    Раньше сюда пролезал недокачанный фрагмент вида "2859.f137.mp4": его
    принимали за результат, переименовывали в "2859.mp4" и отдавали клиенту —
    видео без звука и на пару минут вместо часа.
    """
    if path.is_dir():
        return False

    if path.suffix.lower() not in DOWNLOAD_EXTS:
        return False

    stem = path.stem
    if stem.endswith(INTERMEDIATE_STEM_SUFFIXES):
        return False

    return FORMAT_FRAGMENT_PATTERN.search(stem) is None


def is_subtitle_file(path: Path) -> bool:
    return path.suffix.lower() in SUBTITLE_EXTS


def cleanup_stale_download_artifacts(video_dir: Path, video_id: int, external_id: str) -> None:
    patterns = (
        f"{video_id}.*",
        f"{external_id}.*",
        f"{video_id}*.ytdl",
        f"{external_id}*.ytdl",
    )

    seen_paths: set[Path] = set()
    for pattern in patterns:
        for path in video_dir.glob(pattern):
            if path in seen_paths or path.is_dir() or is_subtitle_file(path):
                continue
            seen_paths.add(path)
            path.unlink(missing_ok=True)


def normalize_download_path(path: Path, video_dir: Path, video_id: int) -> Path:
    target_path = video_dir / f"{video_id}{path.suffix}"

    if path == target_path:
        return path

    if target_path.exists():
        return target_path

    path.rename(target_path)
    return target_path


def normalize_subtitle_paths(original_video_path: Path, normalized_video_path: Path) -> list[Path]:
    subtitle_files = find_subtitle_files(normalized_video_path)
    if subtitle_files or original_video_path.stem == normalized_video_path.stem:
        return subtitle_files

    renamed_subtitle_files: list[Path] = []
    original_stem = original_video_path.stem
    normalized_stem = normalized_video_path.stem
    for subtitle_path in sorted(original_video_path.parent.iterdir()):
        if not subtitle_path.is_file() or subtitle_path.suffix.lower() not in SUBTITLE_EXTS:
            continue
        if subtitle_path.stem.split(".", 1)[0] != original_stem:
            continue

        suffix = subtitle_path.name[len(original_stem):]
        target_path = subtitle_path.with_name(f"{normalized_stem}{suffix}")
        subtitle_path.rename(target_path)
        renamed_subtitle_files.append(target_path)

    return renamed_subtitle_files


def is_video_in_archive(archive_file: Path, external_id: str) -> bool:
    if not archive_file.exists():
        return False

    archive_entry = f"youtube {external_id}"

    return archive_entry in archive_file.read_text().splitlines()


def remove_video_from_archive(archive_file: Path, external_id: str) -> None:
    if not archive_file.exists():
        return

    archive_entry = f"youtube {external_id}"
    filtered_lines = [
        line for line in archive_file.read_text().splitlines()
        if line.strip() != archive_entry
    ]

    content = "\n".join(filtered_lines)
    if content:
        content += "\n"

    archive_file.write_text(content)


def is_permanently_unavailable_error(error: DownloadError) -> bool:
    message = str(error).lower()

    non_permanent_markers = (
        "n challenge solving failed",
        "some formats may be missing",
        "sign in to confirm",
        "not a bot",
        "premieres in",
    )

    if any(marker in message for marker in non_permanent_markers):
        return False

    permanent_markers = (
        # Ровно то, что YouTube отвечает про снятый ролик: "Video unavailable".
        # Формулировка со вставленным "is" нужна отдельно — подстрока
        # "video unavailable" в "this video is unavailable" не находится,
        # и из-за этого видео 2771 набрало 32 неудачные попытки за три часа,
        # уходя в очередь заново каждый цикл.
        "video unavailable",
        "video is unavailable",
        "this video is not available",
        "content isn't available",
        "no longer available",
        "private video",
        "has been removed",
        "deleted video",
        "video has been removed by the uploader",
        "this video is private",
        "the uploader has not made this video available",
    )

    return any(marker in message for marker in permanent_markers)


def is_authentication_required_error(error: DownloadError) -> bool:
    message = str(error).lower()
    return "sign in to confirm" in message or "not a bot" in message


def is_youtube_challenge_runtime_error(error: DownloadError) -> bool:
    message = str(error).lower()
    return (
        "challenge solving failed" in message
        or "only images are available for download" in message
    )
