from __future__ import annotations

import importlib.util
import logging
import re
import shutil
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from video_downloader.domain.models import DownloadJob

LOGGER = logging.getLogger(__name__)

# Из равных по разрешению вариантов берём H.264 + AAC — ровно то, что
# AVPlayer играет без единого перекодирования.
#
# Почему сортировкой, а не условием в селекторе. Условие "[vcodec^=avc1]"
# отсекает видео целиком, а у части роликов (обычно старых) H.264 есть
# только в 480p, тогда как VP9/AV1 доходят до 1080p — и мы молча теряли бы
# разрешение. FORMAT_SORT ставит res первым ключом, поэтому качество не
# падает никогда, а h264 выигрывает лишь при прочих равных.
#
# Фильтровать по "[ext=mp4]" бесполезно: YouTube отдаёт в mp4 не только
# avc1, но и av01, причём по качеству AV1 выигрывает — и "bestvideo*"
# всегда выбирал именно его. Дальше воркер видел несовместимый кодек и
# перекодировал AV1 в H.264: качал компактный файл, жёг процессор и клал
# на диск результат вдвое больше ютубовского же avc1.
#
# У звука задано "acodec^=mp4a" по схожей причине: Dolby Digital Plus
# (itag 328, ec-3, 5.1) приезжает тоже в контейнере m4a и с большим
# битрейтом, так что "bestaudio[ext=m4a]" предпочитал его обычному AAC.
# На tvOS такая дорожка играется молча.
FORMAT_SORT = ("res", "vcodec:h264", "acodec:aac")

YOUTUBE_FORMAT_SELECTOR = (
    "bestvideo*[vcodec!=none][height<=1080]+bestaudio[ext=m4a][acodec^=mp4a]/"
    "bestvideo*[vcodec!=none][height<=1080]+bestaudio[acodec^=mp4a]/"
    "best[vcodec!=none][acodec^=mp4a][height<=1080]/"
    "bestvideo*[vcodec!=none][height<=1080]+bestaudio[acodec!=none]/"
    "best[vcodec!=none][acodec!=none][height<=1080]/"
    "best"
)


class YtDlpLogger:
    def debug(self, message: str) -> None:
        LOGGER.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        LOGGER.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        LOGGER.debug("yt-dlp: %s", message)


def download_video_with_yt_dlp(
    job: DownloadJob,
    output_template: str,
    archive_file: Path,
    *,
    yt_dlp_cookie_file: Path | None = None,
    yt_dlp_cookies_from_browser: tuple[str, ...] | None = None,
    yt_dlp_node_path: Path | None = None,
    subtitle_langs: tuple[str, ...] = (),
) -> None:
    options = build_yt_dlp_options(
        output_template=output_template,
        archive_file=archive_file,
        yt_dlp_cookie_file=yt_dlp_cookie_file,
        yt_dlp_cookies_from_browser=yt_dlp_cookies_from_browser,
        yt_dlp_node_path=yt_dlp_node_path,
        subtitle_langs=subtitle_langs,
    )
    video_url = job.source_url or f"https://www.youtube.com/watch?v={job.external_id}"

    LOGGER.info("Downloading video id=%s name=%s", job.external_id, job.name)

    try:
        with YoutubeDL(options) as ydl:
            ydl.extract_info(video_url, download=True)
    except OSError as error:
        translated_error = translate_cookie_access_error(
            error,
            yt_dlp_cookie_file=yt_dlp_cookie_file,
            yt_dlp_cookies_from_browser=yt_dlp_cookies_from_browser,
        )
        if translated_error is not None:
            raise translated_error from error
        raise
    except DownloadError as error:
        if not is_range_not_satisfiable_error(error):
            raise

        LOGGER.warning(
            "yt-dlp returned HTTP 416 for id=%s external_id=%s. "
            "Removing stale download artifacts and retrying once from scratch.",
            job.id,
            job.external_id,
        )
        raise


def build_yt_dlp_options(
    *,
    output_template: str,
    archive_file: Path,
    yt_dlp_cookie_file: Path | None = None,
    yt_dlp_cookies_from_browser: tuple[str, ...] | None = None,
    yt_dlp_node_path: Path | None = None,
    subtitle_langs: tuple[str, ...] = (),
) -> dict[str, object]:
    js_runtimes = detect_supported_js_runtimes(yt_dlp_node_path)

    if not js_runtimes:
        LOGGER.warning(
            "No supported JavaScript runtime detected for yt-dlp YouTube challenge solving. "
            "Install Deno or Node.js on the worker host."
        )

    if importlib.util.find_spec("yt_dlp_ejs") is None:
        LOGGER.warning(
            "yt-dlp-ejs is not installed in this environment. Remote EJS fetching is enabled, "
            "but installing yt-dlp with its default extras is more reliable."
        )

    postprocessors: list[dict[str, object]] = [
        {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
    ]

    options: dict[str, object] = {
        "outtmpl": output_template,
        "format": YOUTUBE_FORMAT_SELECTOR,
        "format_sort": list(FORMAT_SORT),
        "download_archive": str(archive_file),
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {
                "skip": ["translated_subs"],
            },
        },
        "js_runtimes": js_runtimes,
        "merge_output_format": "mp4",
        "postprocessors": postprocessors,
        "continuedl": False,
        # nopart=True здесь был опасен: yt-dlp писал прямо в конечное имя,
        # и файл, оборванный на середине (рестарт воркера, деплой), внешне
        # не отличался от готового. Суффикс .part возвращает это различие.
        "restrictfilenames": False,
        "noplaylist": True,
        "logger": YtDlpLogger(),
        "quiet": True,
        "no_warnings": True,
    }

    if subtitle_langs:
        options["writesubtitles"] = True
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = build_subtitle_language_patterns(subtitle_langs)
        options["subtitlesformat"] = "vtt/best"

    if yt_dlp_cookie_file is not None:
        options["cookiefile"] = str(yt_dlp_cookie_file)

    if yt_dlp_cookies_from_browser is not None:
        options["cookiesfrombrowser"] = yt_dlp_cookies_from_browser

    return options


def build_subtitle_language_patterns(subtitle_langs: tuple[str, ...]) -> list[str]:
    patterns: list[str] = []
    for lang in subtitle_langs:
        normalized = lang.strip()
        if not normalized:
            continue
        if looks_like_regex(normalized):
            patterns.append(normalized)
            continue
        escaped = re.escape(normalized)
        patterns.append(f"^{escaped}(?:$|[-_].*)")
    return patterns


def looks_like_regex(value: str) -> bool:
    return any(char in value for char in ".^$*+?{}[]\\|()")


def detect_supported_js_runtimes(node_path: Path | None = None) -> dict[str, dict[str, str]]:
    supported_runtimes = {
        "deno": "deno",
        "node": "node",
        "quickjs": "qjs",
        "bun": "bun",
    }
    detected_runtimes = {
        runtime_name: {"path": runtime_path}
        for runtime_name, binary_name in supported_runtimes.items()
        if (runtime_path := shutil.which(binary_name)) is not None
    }
    if node_path is not None:
        detected_runtimes["node"] = {"path": str(node_path)}

    return detected_runtimes


def is_range_not_satisfiable_error(error: DownloadError) -> bool:
    return "http error 416" in str(error).lower()


def translate_cookie_access_error(
    error: OSError,
    *,
    yt_dlp_cookie_file: Path | None,
    yt_dlp_cookies_from_browser: tuple[str, ...] | None,
) -> DownloadError | None:
    if yt_dlp_cookie_file is None and yt_dlp_cookies_from_browser is None:
        return None

    if isinstance(error, FileNotFoundError):
        if yt_dlp_cookie_file is not None:
            message = (
                "yt-dlp could not read the configured cookie file. "
                f"Check VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE: {yt_dlp_cookie_file}"
            )
            return DownloadError(message)

        browser_name = yt_dlp_cookies_from_browser[0]
        profile_hint = ":".join(yt_dlp_cookies_from_browser[1:])
        profile_suffix = f" (profile: {profile_hint})" if profile_hint else ""
        return DownloadError(
            "yt-dlp could not load cookies from the configured browser. "
            f"Check VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER={browser_name}{profile_suffix} "
            "and ensure the browser profile exists on the worker host."
        )

    if isinstance(error, PermissionError):
        if yt_dlp_cookie_file is not None:
            return DownloadError(
                "yt-dlp could not access the configured cookie file due to permissions. "
                f"Check VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE: {yt_dlp_cookie_file}"
            )

        browser_name = yt_dlp_cookies_from_browser[0]
        return DownloadError(
            "yt-dlp could not access the configured browser cookie store due to permissions. "
            f"Check VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER={browser_name} "
            "and the worker user permissions."
        )

    return None
