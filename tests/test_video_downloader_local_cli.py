from __future__ import annotations

import unittest

from video_downloader.entrypoints.cli import build_test_job, extract_youtube_video_id


class VideoDownloaderLocalCliTests(unittest.TestCase):
    def test_extracts_video_id_from_watch_url(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extracts_video_id_from_shorts_url(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://youtube.com/shorts/dQw4w9WgXcQ?si=abc"),
            "dQw4w9WgXcQ",
        )

    def test_extracts_video_id_from_short_link(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ?t=10"),
            "dQw4w9WgXcQ",
        )

    def test_builds_local_test_job_from_url(self) -> None:
        job = build_test_job(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            channel_id=77,
            video_id=None,
            name="Local test",
        )

        self.assertEqual(job.external_id, "dQw4w9WgXcQ")
        self.assertEqual(job.channel_id, 77)
        self.assertEqual(job.name, "Local test")
        self.assertEqual(job.source_url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertGreater(job.id, 0)

    def test_rejects_unsupported_url(self) -> None:
        with self.assertRaises(ValueError):
            extract_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ")


if __name__ == "__main__":
    unittest.main()
