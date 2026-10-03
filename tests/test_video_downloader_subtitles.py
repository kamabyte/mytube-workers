from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from video_downloader.domain.models import DownloadJob
from video_downloader.downloader import download_video
from video_downloader.infrastructure.converters.ffmpeg import AudioStreamInfo, MediaInfo


def playable_media_info(duration_seconds: float = 100.0) -> MediaInfo:
    return MediaInfo(
        container_formats=frozenset({"mp4", "mov"}),
        video_codecs=frozenset({"h264"}),
        audio_streams=(AudioStreamInfo(codec="aac", channels=2),),
        duration_seconds=duration_seconds,
    )


class DownloadVideoSubtitleTests(unittest.TestCase):
    def test_embeds_sidecar_subtitles_without_apple_tv_transcoding(self) -> None:
        with TemporaryDirectory() as temp_dir:
            download_dir = Path(temp_dir)
            channel_dir = download_dir / "7"
            channel_dir.mkdir(parents=True, exist_ok=True)
            video_path = channel_dir / "42.mp4"
            video_path.write_bytes(b"video")
            (channel_dir / "42.en.vtt").write_text("WEBVTT\n")
            (channel_dir / "42.ru.vtt").write_text("WEBVTT\n")
            archive_file = download_dir / ".downloaded-archive.txt"

            with (
                patch("video_downloader.downloader.probe_media", return_value=playable_media_info()),
                patch("video_downloader.downloader.convert_to_mp4", return_value=video_path) as convert_mock,
            ):
                result = download_video(
                    DownloadJob(id=42, external_id="ext42", name="Video", channel_id=7),
                    download_dir,
                    archive_file,
                    transcode_for_apple_tv=False,
                )

        self.assertEqual(result.path, video_path)
        self.assertEqual(convert_mock.call_count, 1)
        self.assertFalse(convert_mock.call_args.kwargs["transcode_video"])
        self.assertFalse(convert_mock.call_args.kwargs["transcode_audio"])
        self.assertEqual(
            [path.name for path in convert_mock.call_args.kwargs["subtitle_files"]],
            ["42.en.vtt", "42.ru.vtt"],
        )

    def test_renames_legacy_sidecar_subtitles_before_embedding(self) -> None:
        with TemporaryDirectory() as temp_dir:
            download_dir = Path(temp_dir)
            channel_dir = download_dir / "7"
            channel_dir.mkdir(parents=True, exist_ok=True)
            legacy_video_path = channel_dir / "legacy-id.mp4"
            legacy_video_path.write_bytes(b"video")
            (channel_dir / "legacy-id.en.vtt").write_text("WEBVTT\n")
            archive_file = download_dir / ".downloaded-archive.txt"
            expected_path = channel_dir / "42.mp4"

            with (
                patch("video_downloader.downloader.probe_media", return_value=playable_media_info()),
                patch("video_downloader.downloader.convert_to_mp4", return_value=expected_path) as convert_mock,
            ):
                result = download_video(
                    DownloadJob(id=42, external_id="legacy-id", name="Video", channel_id=7),
                    download_dir,
                    archive_file,
                    transcode_for_apple_tv=False,
                )

            renamed_subtitle_path = channel_dir / "42.en.vtt"
            self.assertEqual(result.path, expected_path)
            self.assertFalse((channel_dir / "legacy-id.en.vtt").exists())
            self.assertTrue(renamed_subtitle_path.exists())
            self.assertEqual(
                [path.name for path in convert_mock.call_args.kwargs["subtitle_files"]],
                ["42.en.vtt"],
            )


if __name__ == "__main__":
    unittest.main()
