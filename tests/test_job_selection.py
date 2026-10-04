from __future__ import annotations

import unittest

from sqlalchemy import create_engine, text

from video_downloader.infrastructure.sources.db_source import DbJobSource

# Только нужные запросу колонки из миграций mytube-api (videos, channels, channel_video).
SCHEMA = [
    """
    CREATE TABLE channels (
        id INTEGER PRIMARY KEY,
        download_on_demand TINYINT(1) NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY,
        external_id VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        channel_id INTEGER NOT NULL,
        duration_seconds INTEGER,
        is_downloaded TINYINT(1) NOT NULL DEFAULT 0,
        is_unavailable TINYINT(1) NOT NULL DEFAULT 0,
        published_at DATETIME,
        download_requested_at DATETIME
    )
    """,
    """
    CREATE TABLE channel_video (
        channel_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL
    )
    """,
]

AUTO = 1
ON_DEMAND = 2


class DbJobSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            for statement in SCHEMA:
                connection.execute(text(statement))
            connection.execute(text("INSERT INTO channels (id, download_on_demand) VALUES (1, 0), (2, 1)"))
        self.source = DbJobSource(self.engine)

    def video(self, video_id: int, *channels: int, published_at: str = "2026-01-01", **columns) -> None:
        row = {
            "id": video_id,
            "external_id": f"ext{video_id}",
            "name": f"Video {video_id}",
            "channel_id": channels[0],
            "duration_seconds": 60,
            "published_at": published_at,
            "is_downloaded": 0,
            "is_unavailable": 0,
            "download_requested_at": None,
            **columns,
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO videos (id, external_id, name, channel_id, duration_seconds, published_at,"
                    " is_downloaded, is_unavailable, download_requested_at)"
                    " VALUES (:id, :external_id, :name, :channel_id, :duration_seconds, :published_at,"
                    " :is_downloaded, :is_unavailable, :download_requested_at)"
                ),
                row,
            )
            for channel_id in channels:
                connection.execute(
                    text("INSERT INTO channel_video (channel_id, video_id) VALUES (:channel_id, :video_id)"),
                    {"channel_id": channel_id, "video_id": video_id},
                )

    def pending_ids(self, limit: int = 10, exclude=()) -> list[int]:
        return [job.id for job in self.source.fetch_pending(limit, exclude=exclude)]

    def test_skips_the_catalog_of_on_demand_channels(self) -> None:
        self.video(1, AUTO)
        self.video(2, ON_DEMAND)
        self.video(3, ON_DEMAND, download_requested_at="2026-10-04 10:00:00")

        self.assertEqual(self.pending_ids(), [3, 1])

    def test_requested_videos_go_first_in_request_order(self) -> None:
        self.video(1, AUTO, published_at="2020-01-01")
        self.video(2, ON_DEMAND, download_requested_at="2026-10-04 10:05:00")
        self.video(3, ON_DEMAND, download_requested_at="2026-10-04 10:00:00")
        self.video(4, AUTO, published_at="2019-01-01")

        self.assertEqual(self.pending_ids(), [3, 2, 4, 1])

    def test_a_video_is_queued_when_any_source_downloads_everything(self) -> None:
        self.video(1, ON_DEMAND, AUTO)

        self.assertEqual(self.pending_ids(), [1])

    def test_skips_downloaded_and_unavailable_videos(self) -> None:
        self.video(1, AUTO, is_downloaded=1)
        self.video(2, AUTO, is_unavailable=1)
        self.video(3, ON_DEMAND, is_unavailable=1, download_requested_at="2026-10-04 10:00:00")

        self.assertEqual(self.pending_ids(), [])

    def test_excludes_already_attempted_videos(self) -> None:
        self.video(1, AUTO, published_at="2020-01-01")
        self.video(2, AUTO, published_at="2021-01-01")

        self.assertEqual(self.pending_ids(limit=1), [1])
        self.assertEqual(self.pending_ids(limit=1, exclude=[1]), [2])
        self.assertEqual(self.pending_ids(exclude=[1, 2]), [])


if __name__ == "__main__":
    unittest.main()
