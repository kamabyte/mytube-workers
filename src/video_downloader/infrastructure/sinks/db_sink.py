from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from video_downloader.domain.models import DownloadJob, DownloadResult


class DbResultSink:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def mark_success(self, job: DownloadJob, result: DownloadResult | None) -> bool:
        file_size = None if result is None else result.file_size
        query = text(
            """
            UPDATE videos
            SET is_downloaded = :downloaded,
                file_size = :file_size,
                downloaded_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :video_id
              AND is_downloaded = :not_downloaded
            """
        )

        with self._engine.begin() as connection:
            applied = connection.execute(
                query,
                {
                    "downloaded": True,
                    "file_size": file_size,
                    "not_downloaded": False,
                    "video_id": job.id,
                },
            )

        return applied.rowcount > 0

    def mark_unavailable(self, job: DownloadJob) -> bool:
        """Помечает видео недоступным, не трогая is_downloaded.

        is_downloaded остаётся нулём — файла-то нет, и врать о нём не нужно.
        Из очереди видео уходит по is_unavailable, а выдачу API этот флаг
        не трогает вовсе: уже скачанный ролик, снятый потом с YouTube,
        должен продолжать играться. В том и смысл архива.
        """
        query = text(
            """
            UPDATE videos
            SET is_unavailable = :unavailable,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :video_id
              AND is_unavailable = :available
            """
        )

        with self._engine.begin() as connection:
            applied = connection.execute(
                query,
                {"unavailable": True, "available": False, "video_id": job.id},
            )

        return applied.rowcount > 0

    def mark_failure(self, job: DownloadJob, error: Exception) -> None:
        return None
