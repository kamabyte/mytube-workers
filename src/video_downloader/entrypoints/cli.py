from __future__ import annotations

import argparse
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import zlib

from video_downloader.application.process_jobs import ProcessJobs
from video_downloader.config import load_config
from video_downloader.domain.models import DownloadJob
from video_downloader.infrastructure.persistence.db import create_db_engine
from video_downloader.infrastructure.processors.yt_dlp_processor import YtDlpProcessor
from video_downloader.infrastructure.recorders.db_recorder import DbRunRecorder, NullRunRecorder
from video_downloader.infrastructure.sinks.db_sink import DbResultSink
from video_downloader.infrastructure.sources.db_source import DbJobSource


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    config = load_config(
        env_file=args.env_file,
        download_dir=args.download_dir,
        poll_interval=args.interval,
        batch_size=args.limit,
    )

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "test-url":
        run_test_url(args, config)
        return

    service = build_service(args, config)

    if args.command == "once":
        service.run_once()
        return

    service.run_worker()


def build_service(args: argparse.Namespace, config) -> ProcessJobs:
    processor = YtDlpProcessor(
        download_dir=config.download_dir,
        archive_file=config.archive_file,
        transcode_for_apple_tv=args.transcode_for_apple_tv or config.transcode_for_apple_tv,
        transcode_options=config.transcode_options,
        yt_dlp_cookie_file=config.yt_dlp_cookie_file,
        yt_dlp_cookies_from_browser=config.yt_dlp_cookies_from_browser,
        yt_dlp_node_path=config.yt_dlp_node_path,
        subtitle_langs=config.subtitle_langs,
    )

    engine = create_db_engine(config.db_url)
    return ProcessJobs(
        source=DbJobSource(engine),
        sink=DbResultSink(engine),
        processor=processor,
        recorder=NullRunRecorder() if args.dry_run else DbRunRecorder(engine),
        poll_interval=config.poll_interval,
        batch_size=config.batch_size,
        dry_run=args.dry_run,
    )


def run_test_url(args: argparse.Namespace, config) -> None:
    processor = YtDlpProcessor(
        download_dir=config.download_dir,
        archive_file=config.archive_file,
        transcode_for_apple_tv=args.transcode_for_apple_tv or config.transcode_for_apple_tv,
        transcode_options=config.transcode_options,
        yt_dlp_cookie_file=config.yt_dlp_cookie_file,
        yt_dlp_cookies_from_browser=config.yt_dlp_cookies_from_browser,
        yt_dlp_node_path=config.yt_dlp_node_path,
        subtitle_langs=config.subtitle_langs,
    )
    job = build_test_job(
        args.url,
        channel_id=args.channel_id,
        video_id=args.video_id,
        name=args.name,
    )
    result = processor.process(job)
    print(result.path)


def build_test_job(url: str, *, channel_id: int, video_id: int | None, name: str | None) -> DownloadJob:
    external_id = extract_youtube_video_id(url)
    resolved_video_id = video_id if video_id is not None else zlib.crc32(external_id.encode("utf-8")) or 1
    return DownloadJob(
        id=resolved_video_id,
        external_id=external_id,
        name=name or external_id,
        channel_id=channel_id,
        source_url=url,
    )


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
        if video_id:
            return video_id

    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return video_id

        for prefix in ("/shorts/", "/live/", "/embed/"):
            if parsed.path.startswith(prefix):
                video_id = parsed.path[len(prefix):].split("/", 1)[0]
                if video_id:
                    return video_id

    raise ValueError(f"Unsupported YouTube URL: {url}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download videos from the Laravel videos table.")
    subparsers = parser.add_subparsers(dest="command")

    for command_name in ("once", "worker"):
        command = subparsers.add_parser(command_name)
        command.add_argument("--env-file", type=Path, default=None, help="Path to the Laravel .env file.")
        command.add_argument("--download-dir", type=Path, default=None, help="Directory where videos will be stored.")
        command.add_argument("--limit", type=int, default=100, help="How many pending videos to fetch per cycle.")
        command.add_argument("--dry-run", action="store_true", help="Read pending videos without downloading or updating rows.")
        command.add_argument(
            "--transcode-for-apple-tv",
            action="store_true",
            help="After remux/download, transcode incompatible files to H.264/AAC MP4.",
        )

    worker = subparsers.choices["worker"]
    worker.add_argument("--interval", type=int, default=300, help="Polling interval in seconds.")

    once = subparsers.choices["once"]
    once.add_argument("--interval", type=int, default=300, help=argparse.SUPPRESS)

    test_url = subparsers.add_parser("test-url", help="Download one YouTube URL locally through the normal pipeline.")
    test_url.add_argument("url", help="YouTube URL to download.")
    test_url.add_argument("--env-file", type=Path, default=None, help="Path to the worker .env file.")
    test_url.add_argument("--download-dir", type=Path, default=None, help="Directory where videos will be stored.")
    test_url.add_argument(
        "--transcode-for-apple-tv",
        action="store_true",
        help="After remux/download, transcode incompatible files to H.264/AAC MP4.",
    )
    test_url.add_argument(
        "--channel-id",
        type=int,
        default=0,
        help="Channel folder name to use under the download directory.",
    )
    test_url.add_argument(
        "--video-id",
        type=int,
        default=None,
        help="Numeric local video id used for the output filename. Defaults to a stable hash of the YouTube id.",
    )
    test_url.add_argument(
        "--name",
        default=None,
        help="Optional display name used in logs.",
    )
    test_url.add_argument("--interval", type=int, default=300, help=argparse.SUPPRESS)
    test_url.add_argument("--limit", type=int, default=100, help=argparse.SUPPRESS)
    test_url.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)

    return parser
