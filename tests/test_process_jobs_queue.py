from __future__ import annotations

import unittest
from pathlib import Path

from video_downloader.application.process_jobs import ProcessJobs
from video_downloader.domain.models import DownloadJob, DownloadResult


class Sink:
    def mark_success(self, job, result) -> bool:
        return True

    def mark_failure(self, job, error) -> None:
        return None

    def mark_unavailable(self, job) -> bool:
        return True


class Recorder:
    def start(self, job) -> int | None:
        return None

    def finish(self, run_id, *, status, result=None, error=None) -> None:
        return None


def job(job_id: int) -> DownloadJob:
    return DownloadJob(id=job_id, external_id=f"ext{job_id}", name=f"Video {job_id}", channel_id=1, duration_seconds=60)


class QueueSource:
    """Очередь, в которую можно подкинуть запрос посреди прохода."""

    def __init__(self, queue: list[int]) -> None:
        self.queue = queue

    def fetch_pending(self, limit: int, exclude=()) -> list[DownloadJob]:
        return [job(job_id) for job_id in self.queue if job_id not in exclude][:limit]


class Processor:
    def __init__(self, source: QueueSource, *, fail: set[int] = frozenset(), on_first=None) -> None:
        self.source = source
        self.fail = fail
        self.on_first = on_first
        self.seen: list[int] = []

    def process(self, current: DownloadJob) -> DownloadResult:
        self.seen.append(current.id)
        if len(self.seen) == 1 and self.on_first:
            self.on_first()
        if current.id in self.fail:
            raise RuntimeError("boom")
        # Скачанное уходит из очереди, как после mark_success.
        self.source.queue.remove(current.id)
        return DownloadResult(path=Path(f"/tmp/{current.id}.mp4"), file_size=1, download_seconds=1, transcode_seconds=0)


def run(source: QueueSource, processor: Processor, batch_size: int = 10) -> int:
    return ProcessJobs(
        source=source,
        sink=Sink(),
        processor=processor,
        recorder=Recorder(),
        poll_interval=1,
        batch_size=batch_size,
        dry_run=False,
    ).run_once()


class RunOnceQueueTests(unittest.TestCase):
    def test_a_request_made_mid_pass_goes_next(self) -> None:
        source = QueueSource([1, 2, 3])
        # Пока качается первое, из веба попросили видео 9 — источник ставит его в начало.
        processor = Processor(source, on_first=lambda: source.queue.insert(0, 9))

        run(source, processor)

        self.assertEqual(processor.seen, [1, 9, 2, 3])

    def test_a_failing_video_is_not_retried_within_a_pass(self) -> None:
        source = QueueSource([1, 2])
        processor = Processor(source, fail={1})

        run(source, processor)

        self.assertEqual(processor.seen, [1, 2])

    def test_stops_at_the_batch_size(self) -> None:
        source = QueueSource([1, 2, 3])
        processor = Processor(source)

        self.assertEqual(run(source, processor, batch_size=2), 2)
        self.assertEqual(processor.seen, [1, 2])


if __name__ == "__main__":
    unittest.main()
