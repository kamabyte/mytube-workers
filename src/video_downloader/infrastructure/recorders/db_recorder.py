from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from video_downloader.domain.models import DownloadJob, DownloadResult

LOGGER = logging.getLogger(__name__)

# Тексты падений бывают длинными (простыни от yt-dlp), а читают всё равно
# начало. Режем, чтобы журнал попыток не раздувал базу.
MAX_ERROR_LENGTH = 2000


class DbRunRecorder:
    """Пишет журнал попыток в video_download_runs.

    Учёт статистики не должен ронять загрузку: любая ошибка записи логируется
    и проглатывается. Потерять строку журнала не жалко, потерять видео — жалко.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start(self, job: DownloadJob) -> int | None:
        query = text(
            """
            INSERT INTO video_download_runs (video_id, started_at, status)
            VALUES (:video_id, CURRENT_TIMESTAMP, 'running')
            """
        )

        try:
            with self._engine.begin() as connection:
                result = connection.execute(query, {"video_id": job.id})
                return result.lastrowid
        except SQLAlchemyError:
            LOGGER.warning("Could not open a download run row for id=%s", job.id, exc_info=True)
            return None

    def finish(
        self,
        run_id: int | None,
        *,
        status: str,
        result: DownloadResult | None = None,
        error: Exception | None = None,
    ) -> None:
        if run_id is None:
            return

        query = text(
            """
            UPDATE video_download_runs
            SET finished_at = CURRENT_TIMESTAMP,
                status = :status,
                download_seconds = :download_seconds,
                transcode_seconds = :transcode_seconds,
                file_size = :file_size,
                error = :error
            WHERE id = :run_id
            """
        )

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    query,
                    {
                        "run_id": run_id,
                        "status": status,
                        "download_seconds": round_seconds(result.download_seconds if result else None),
                        "transcode_seconds": round_seconds(result.transcode_seconds if result else None),
                        "file_size": result.file_size if result else None,
                        "error": truncate_error(error),
                    },
                )
        except SQLAlchemyError:
            LOGGER.warning("Could not close download run row id=%s", run_id, exc_info=True)


class NullRunRecorder:
    """Заглушка для dry-run и локальных прогонов без базы."""

    def start(self, job: DownloadJob) -> int | None:
        return None

    def finish(
        self,
        run_id: int | None,
        *,
        status: str,
        result: DownloadResult | None = None,
        error: Exception | None = None,
    ) -> None:
        return None


def round_seconds(value: float | None) -> int | None:
    return None if value is None else max(0, round(value))


def truncate_error(error: Exception | None) -> str | None:
    if error is None:
        return None

    message = f"{type(error).__name__}: {error}".strip()
    if len(message) <= MAX_ERROR_LENGTH:
        return message

    return message[: MAX_ERROR_LENGTH - 1] + "…"
