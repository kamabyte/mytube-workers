from __future__ import annotations

from pathlib import Path

from video_downloader.domain.models import DownloadJob, DownloadResult
from video_downloader.downloader import download_video
from video_downloader.infrastructure.converters.ffmpeg import FFmpegTranscodeOptions


class YtDlpProcessor:
    def __init__(
        self,
        *,
        download_dir: Path,
        archive_file: Path,
        transcode_for_apple_tv: bool = False,
        transcode_options: FFmpegTranscodeOptions | None = None,
        yt_dlp_cookie_file: Path | None = None,
        yt_dlp_cookies_from_browser: tuple[str, ...] | None = None,
        yt_dlp_node_path: Path | None = None,
        subtitle_langs: tuple[str, ...] = (),
    ) -> None:
        self._download_dir = download_dir
        self._archive_file = archive_file
        self._transcode_for_apple_tv = transcode_for_apple_tv
        self._transcode_options = transcode_options
        self._yt_dlp_cookie_file = yt_dlp_cookie_file
        self._yt_dlp_cookies_from_browser = yt_dlp_cookies_from_browser
        self._yt_dlp_node_path = yt_dlp_node_path
        self._subtitle_langs = subtitle_langs

    def process(self, job: DownloadJob) -> DownloadResult:
        outcome = download_video(
            job,
            self._download_dir,
            self._archive_file,
            transcode_for_apple_tv=self._transcode_for_apple_tv,
            transcode_options=self._transcode_options,
            yt_dlp_cookie_file=self._yt_dlp_cookie_file,
            yt_dlp_cookies_from_browser=self._yt_dlp_cookies_from_browser,
            yt_dlp_node_path=self._yt_dlp_node_path,
            subtitle_langs=self._subtitle_langs,
        )
        return DownloadResult(
            path=outcome.path,
            file_size=outcome.path.stat().st_size,
            download_seconds=outcome.download_seconds,
            transcode_seconds=outcome.transcode_seconds,
        )
