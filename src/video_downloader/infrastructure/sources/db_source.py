from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from video_downloader.domain.models import DownloadJob


class DbJobSource:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def fetch_pending(self, limit: int) -> list[DownloadJob]:
        query = text(
            """
            SELECT id, external_id, name, channel_id, duration_seconds
            FROM videos
            WHERE is_downloaded = :is_downloaded
              AND is_unavailable = :is_unavailable
            ORDER BY published_at ASC, id ASC
            LIMIT :limit
            """
        )

        with self._engine.connect() as connection:
            rows = connection.execute(query, {"is_downloaded": False, "is_unavailable": False, "limit": limit}).mappings().all()

        return [DownloadJob(**row) for row in rows]
