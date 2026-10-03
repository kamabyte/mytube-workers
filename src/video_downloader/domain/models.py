from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DownloadJob:
    id: int
    external_id: str
    name: str
    channel_id: int
    duration_seconds: int | None = None
    source_url: str | None = None


@dataclass(slots=True)
class DownloadOutcome:
    """Результат работы загрузчика вместе с тем, во что обошлись его фазы.

    Фазы разделены намеренно: на замерах по журналу ffmpeg съедал 92% времени,
    и без разбивки «загрузка идёт долго» выглядит как проблема сети.
    """

    path: Path
    download_seconds: float = 0.0
    transcode_seconds: float = 0.0


@dataclass(slots=True)
class DownloadResult:
    path: Path | None
    file_size: int | None = None
    download_seconds: float | None = None
    transcode_seconds: float | None = None
