from __future__ import annotations

import logging
import time

from yt_dlp.utils import DownloadError

from video_downloader.domain.models import DownloadJob
from video_downloader.domain.ports import DownloadNotifier, JobProcessor, JobSource, ResultSink, RunRecorder
from video_downloader.downloader import (
    MediaValidationError,
    is_authentication_required_error,
    is_permanently_unavailable_error,
    is_youtube_challenge_runtime_error,
)

LOGGER = logging.getLogger(__name__)


class ProcessJobs:
    def __init__(
        self,
        *,
        source: JobSource,
        sink: ResultSink,
        processor: JobProcessor,
        recorder: RunRecorder,
        poll_interval: int,
        batch_size: int,
        dry_run: bool,
        notifier: DownloadNotifier | None = None,
    ) -> None:
        self._source = source
        self._sink = sink
        self._processor = processor
        self._recorder = recorder
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._dry_run = dry_run
        self._notifier = notifier

    def run_once(self) -> int:
        # Задания берём по одному, а не пачкой: видео, которое попросили
        # посреди прохода, встаёт следующим, а не ждёт, пока скачается вся пачка.
        # Уже взятые в этом проходе исключаем — упавшее видео остаётся в очереди
        # и иначе вернулось бы снова.
        attempted: list[int] = []
        processed = 0

        while len(attempted) < self._batch_size:
            jobs = self._source.fetch_pending(1, exclude=attempted)
            if not jobs:
                break

            job = jobs[0]
            attempted.append(job.id)

            if self._dry_run:
                LOGGER.info("Dry run video id=%s external_id=%s name=%s", job.id, job.external_id, job.name)
                processed += 1
                continue

            processed += self._process_job(job)

        if not attempted:
            LOGGER.info("No pending videos found.")

        return processed

    def run_worker(self) -> None:
        LOGGER.info(
            "Worker started. Poll interval=%ss batch_size=%s dry_run=%s",
            self._poll_interval,
            self._batch_size,
            self._dry_run,
        )

        while True:
            processed = self.run_once()
            LOGGER.info("Cycle complete. processed=%s sleeping=%ss", processed, self._poll_interval)
            time.sleep(self._poll_interval)

    def _process_job(self, job: DownloadJob) -> int:
        run_id = self._recorder.start(job)
        try:
            result = self._processor.process(job)
            self._recorder.finish(run_id, status="ok", result=result)
            updated = self._sink.mark_success(job, result)
            if updated:
                LOGGER.info(
                    "Marked as downloaded. id=%s path=%s file_size=%s download=%.0fs ffmpeg=%.0fs",
                    job.id,
                    result.path,
                    result.file_size,
                    result.download_seconds or 0.0,
                    result.transcode_seconds or 0.0,
                )
                # После коммита в базу: api читает видео уже скачанным.
                if self._notifier is not None:
                    self._notifier.downloaded(job)
                return 1

            LOGGER.warning("Video was downloaded but not updated. id=%s", job.id)
            return 0
        except DownloadError as error:
            return self._handle_download_error(job, run_id, error)
        except MediaValidationError as error:
            # Файл скачался, но непригоден. Трассировка тут ничего не добавляет:
            # сообщение уже говорит, что именно не сошлось.
            self._recorder.finish(run_id, status="failed", error=error)
            self._sink.mark_failure(job, error)
            LOGGER.error(
                "Download failed validation and was discarded. id=%s external_id=%s reason=%s",
                job.id,
                job.external_id,
                error,
            )
            return 0
        except Exception as error:
            self._recorder.finish(run_id, status="failed", error=error)
            self._sink.mark_failure(job, error)
            LOGGER.exception("Failed to download video. id=%s external_id=%s", job.id, job.external_id)
            return 0

    def _handle_download_error(self, job: DownloadJob, run_id: int | None, error: DownloadError) -> int:
        if is_permanently_unavailable_error(error):
            self._recorder.finish(run_id, status="unavailable", error=error)
            updated = self._sink.mark_unavailable(job)
            if updated:
                LOGGER.warning(
                    "Video is no longer available on YouTube and was taken out of the queue. "
                    "id=%s external_id=%s",
                    job.id,
                    job.external_id,
                )
                return 1

            LOGGER.warning(
                "Video is unavailable but row was not updated. id=%s external_id=%s",
                job.id,
                job.external_id,
            )
            return 0

        if is_authentication_required_error(error):
            LOGGER.error(
                "YouTube requested authentication for id=%s external_id=%s. "
                "Configure VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER "
                "(example: firefox or chrome:Default) or VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE.",
                job.id,
                job.external_id,
            )
        elif is_youtube_challenge_runtime_error(error):
            LOGGER.error(
                "yt-dlp could not solve YouTube's JavaScript challenge for id=%s external_id=%s. "
                "Install Deno or Node.js on the worker host and refresh the worker environment "
                "with `uv sync` so yt-dlp's EJS support is available.",
                job.id,
                job.external_id,
            )

        self._recorder.finish(run_id, status="failed", error=error)
        self._sink.mark_failure(job, error)
        LOGGER.exception("Failed to download video. id=%s external_id=%s", job.id, job.external_id)
        return 0
