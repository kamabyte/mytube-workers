from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from video_downloader.domain.models import DownloadJob

# Что скачивать — то же условие, что Video::awaitingDownload() в mytube-api:
# видео попросили (download_requested_at) или хоть один его источник качает
# всё подряд (канал без download_on_demand). Каталог каналов «по запросу»
# без запроса не трогаем.
#
# Порядок: сначала запрошенные — в порядке запросов, потом очередь каналов
# с автоскачиванием — от старых видео к новым, как раньше.
PENDING_QUERY = text(
    """
    SELECT v.id, v.external_id, v.name, v.channel_id, v.duration_seconds
    FROM videos v
    WHERE v.is_downloaded = :is_downloaded
      AND v.is_unavailable = :is_unavailable
      AND (
        v.download_requested_at IS NOT NULL
        OR EXISTS (
          SELECT 1
          FROM channel_video cv
          JOIN channels c ON c.id = cv.channel_id
          WHERE cv.video_id = v.id
            AND c.download_on_demand = :on_demand
        )
      )
      AND v.id NOT IN :exclude
    ORDER BY v.download_requested_at IS NULL,
             v.download_requested_at ASC,
             v.published_at ASC,
             v.id ASC
    LIMIT :limit
    """
).bindparams(bindparam("exclude", expanding=True))


class DbJobSource:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def fetch_pending(self, limit: int, exclude: Collection[int] = ()) -> list[DownloadJob]:
        params = {
            "is_downloaded": False,
            "is_unavailable": False,
            "on_demand": False,
            "exclude": list(exclude),
            "limit": limit,
        }

        with self._engine.connect() as connection:
            rows = connection.execute(PENDING_QUERY, params).mappings().all()

        return [DownloadJob(**row) for row in rows]
