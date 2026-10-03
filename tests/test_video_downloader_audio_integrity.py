from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from video_downloader.domain.models import DownloadJob
from video_downloader.downloader import (
    MediaValidationError,
    download_video,
    find_existing_download,
    is_finished_download,
)
from video_downloader.infrastructure.converters.ffmpeg import (
    AudioStreamInfo,
    MediaInfo,
    needs_audio_transcode,
    needs_video_transcode,
)


def media_info(
    *,
    audio: tuple[AudioStreamInfo, ...] = (AudioStreamInfo(codec="aac", channels=2),),
    duration_seconds: float | None = 100.0,
    video_codecs: frozenset[str] = frozenset({"h264"}),
    container_formats: frozenset[str] = frozenset({"mp4", "mov"}),
) -> MediaInfo:
    return MediaInfo(
        container_formats=container_formats,
        video_codecs=video_codecs,
        audio_streams=audio,
        duration_seconds=duration_seconds,
    )


class FinishedDownloadDetectionTests(unittest.TestCase):
    """Огрызки yt-dlp не должны приниматься за готовую загрузку."""

    def test_rejects_yt_dlp_intermediates(self) -> None:
        for name in (
            "2859.f137.mp4",      # отдельная видеодорожка до слияния
            "2859.f140.m4a",      # отдельная аудиодорожка
            "2859.temp.mp4",      # результат слияния до переименования
            "2859.transcoding.mp4",  # наш собственный ffmpeg в процессе
            "2859.mp4.part",      # недокачанный файл
            "2859.en.vtt",        # субтитры
        ):
            with self.subTest(name=name):
                self.assertFalse(is_finished_download(Path("/tmp") / name))

    def test_accepts_real_downloads(self) -> None:
        for name in ("2859.mp4", "2859.mkv", "2859.webm"):
            with self.subTest(name=name):
                self.assertTrue(is_finished_download(Path("/tmp") / name))

    def test_find_existing_download_skips_fragment(self) -> None:
        with TemporaryDirectory() as temp_dir:
            video_dir = Path(temp_dir)
            (video_dir / "2859.f137.mp4").write_bytes(b"partial")
            self.assertIsNone(find_existing_download(video_dir, 2859, "ext"))

            (video_dir / "2859.mp4").write_bytes(b"done")
            self.assertEqual(find_existing_download(video_dir, 2859, "ext"), video_dir / "2859.mp4")


class DownloadValidationTests(unittest.TestCase):
    def _run(self, info: MediaInfo, *, duration_seconds: int | None) -> tuple[Path, Path]:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        download_dir = Path(temp_dir.name)
        channel_dir = download_dir / "7"
        channel_dir.mkdir(parents=True, exist_ok=True)
        video_path = channel_dir / "42.mp4"
        video_path.write_bytes(b"video")
        archive_file = download_dir / ".downloaded-archive.txt"
        archive_file.write_text("youtube ext42\n")

        job = DownloadJob(
            id=42,
            external_id="ext42",
            name="Video",
            channel_id=7,
            duration_seconds=duration_seconds,
        )

        with patch("video_downloader.downloader.probe_media", return_value=info):
            with self.assertRaises(MediaValidationError) as caught:
                download_video(job, download_dir, archive_file, transcode_for_apple_tv=False)

        self.exception_message = str(caught.exception)
        return video_path, archive_file

    def test_rejects_file_without_audio(self) -> None:
        video_path, archive_file = self._run(media_info(audio=()), duration_seconds=100)

        self.assertIn("no audio stream", self.exception_message)
        # Битый файл удалён, запись в архиве снята — иначе задача зависнет навсегда.
        self.assertFalse(video_path.exists())
        self.assertEqual(archive_file.read_text(), "")

    def test_rejects_truncated_file(self) -> None:
        video_path, _ = self._run(media_info(duration_seconds=123.0), duration_seconds=4376)

        self.assertIn("duration mismatch", self.exception_message)
        self.assertFalse(video_path.exists())

    def test_accepts_small_duration_drift(self) -> None:
        with TemporaryDirectory() as temp_dir:
            download_dir = Path(temp_dir)
            channel_dir = download_dir / "7"
            channel_dir.mkdir(parents=True, exist_ok=True)
            video_path = channel_dir / "42.mp4"
            video_path.write_bytes(b"video")

            job = DownloadJob(id=42, external_id="ext42", name="Video", channel_id=7, duration_seconds=2551)
            with patch("video_downloader.downloader.probe_media", return_value=media_info(duration_seconds=2550.48)):
                result = download_video(
                    job,
                    download_dir,
                    download_dir / ".downloaded-archive.txt",
                    transcode_for_apple_tv=False,
                )

            self.assertEqual(result.path, video_path)


class CompatibilityRuleTests(unittest.TestCase):
    def test_multichannel_aac_needs_audio_transcode(self) -> None:
        info = media_info(audio=(AudioStreamInfo(codec="aac", channels=6),))
        self.assertTrue(needs_audio_transcode(info))
        self.assertFalse(needs_video_transcode(info))

    def test_eac3_needs_audio_transcode(self) -> None:
        info = media_info(audio=(AudioStreamInfo(codec="eac3", channels=6),))
        self.assertTrue(needs_audio_transcode(info))

    def test_opus_needs_audio_transcode(self) -> None:
        info = media_info(audio=(AudioStreamInfo(codec="opus", channels=2),))
        self.assertTrue(needs_audio_transcode(info))

    def test_stereo_aac_in_mp4_is_left_alone(self) -> None:
        info = media_info()
        self.assertFalse(needs_audio_transcode(info))
        self.assertFalse(needs_video_transcode(info))

    def test_non_mp4_container_needs_video_transcode(self) -> None:
        info = media_info(container_formats=frozenset({"matroska", "webm"}))
        self.assertTrue(needs_video_transcode(info))

    def test_missing_audio_never_requests_audio_transcode(self) -> None:
        self.assertFalse(needs_audio_transcode(media_info(audio=())))


if __name__ == "__main__":
    unittest.main()
