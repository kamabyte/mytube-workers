from __future__ import annotations

from typing import Protocol

from .models import DownloadJob, DownloadResult


class JobSource(Protocol):
    def fetch_pending(self, limit: int) -> list[DownloadJob]: ...


class ResultSink(Protocol):
    def mark_success(self, job: DownloadJob, result: DownloadResult | None) -> bool: ...
    def mark_failure(self, job: DownloadJob, error: Exception) -> None: ...

    # Видео снято с публикации: качать больше нечего, и в очередь
    # возвращать его нельзя.
    def mark_unavailable(self, job: DownloadJob) -> bool: ...


class JobProcessor(Protocol):
    def process(self, job: DownloadJob) -> DownloadResult: ...


class RunRecorder(Protocol):
    """Журнал попыток загрузки — отдельно от каталога видео.

    start() открывает строку, finish() её закрывает. Если воркер умрёт
    посередине, строка останется в статусе running: это и есть признак
    прерванной попытки, а не потеря данных.
    """

    def start(self, job: DownloadJob) -> int | None: ...

    def finish(
        self,
        run_id: int | None,
        *,
        status: str,
        result: DownloadResult | None = None,
        error: Exception | None = None,
    ) -> None: ...
