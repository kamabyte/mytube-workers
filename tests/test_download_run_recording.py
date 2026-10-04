from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import create_engine, text
from yt_dlp.utils import DownloadError

from video_downloader.application.process_jobs import ProcessJobs
from video_downloader.domain.models import DownloadJob, DownloadResult
from video_downloader.downloader import MediaValidationError
from video_downloader.infrastructure.recorders.db_recorder import (
    DbRunRecorder,
    NullRunRecorder,
    truncate_error,
)

JOB = DownloadJob(id=42, external_id="ext42", name="Video", channel_id=7, duration_seconds=100)

# Повторяет database/migrations/..._create_video_download_runs_table.php.
SCHEMA = """
CREATE TABLE video_download_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    video_id INTEGER NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    status VARCHAR NOT NULL DEFAULT 'running',
    download_seconds INTEGER,
    transcode_seconds INTEGER,
    file_size INTEGER,
    error TEXT
)
"""


class RecordingSpy:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.finished: list[dict] = []

    def start(self, job: DownloadJob) -> int | None:
        self.started.append(job.id)
        return len(self.started)

    def finish(self, run_id, *, status, result=None, error=None) -> None:
        self.finished.append({"run_id": run_id, "status": status, "result": result, "error": error})


class StubSink:
    def __init__(self, updated: bool = True) -> None:
        self.updated = updated
        self.unavailable: list[int] = []

    def mark_success(self, job, result) -> bool:
        return self.updated

    def mark_failure(self, job, error) -> None:
        return None

    def mark_unavailable(self, job) -> bool:
        self.unavailable.append(job.id)
        return self.updated


class StubProcessor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def process(self, job: DownloadJob) -> DownloadResult:
        if self._error is not None:
            raise self._error
        return self._result


def run_once(processor, recorder, sink: StubSink | None = None) -> StubSink:
    class Source:
        def fetch_pending(self, limit: int, exclude=()) -> list[DownloadJob]:
            return [] if JOB.id in exclude else [JOB]

    sink = sink or StubSink()
    ProcessJobs(
        source=Source(),
        sink=sink,
        processor=processor,
        recorder=recorder,
        poll_interval=1,
        batch_size=1,
        dry_run=False,
    ).run_once()
    return sink


class ProcessJobsRecordingTests(unittest.TestCase):
    def test_records_successful_run_with_phase_timings(self) -> None:
        result = DownloadResult(
            path=Path("/tmp/42.mp4"),
            file_size=1234,
            download_seconds=29.4,
            transcode_seconds=8.1,
        )
        spy = RecordingSpy()
        run_once(StubProcessor(result=result), spy)

        self.assertEqual(spy.started, [42])
        self.assertEqual(len(spy.finished), 1)
        self.assertEqual(spy.finished[0]["status"], "ok")
        self.assertEqual(spy.finished[0]["result"].download_seconds, 29.4)
        self.assertEqual(spy.finished[0]["result"].transcode_seconds, 8.1)

    def test_records_failed_run(self) -> None:
        spy = RecordingSpy()
        run_once(StubProcessor(error=MediaValidationError("no audio stream")), spy)

        self.assertEqual(spy.finished[0]["status"], "failed")
        self.assertIn("no audio", str(spy.finished[0]["error"]))

    def test_records_unavailable_video_separately_from_failure(self) -> None:
        # Удалённое видео — не поломка воркера, и в статистике падений
        # ему делать нечего.
        spy = RecordingSpy()
        sink = run_once(StubProcessor(error=DownloadError("This video is not available")), spy)

        self.assertEqual(spy.finished[0]["status"], "unavailable")
        self.assertEqual(sink.unavailable, [42])

    def test_youtube_wording_takes_the_video_out_of_the_queue(self) -> None:
        # Регрессия: YouTube отвечает "Video unavailable", а в маркерах была
        # только форма "this video is not available". Подстрока не совпадала,
        # ошибка считалась временной, и видео 2771 набрало 32 попытки
        # за три часа, возвращаясь в очередь каждый цикл.
        spy = RecordingSpy()
        sink = run_once(
            StubProcessor(error=DownloadError("ERROR: [youtube] p7JgOUHw6pE: Video unavailable")),
            spy,
        )

        self.assertEqual(spy.finished[0]["status"], "unavailable")
        self.assertEqual(sink.unavailable, [42])

    def test_every_started_run_is_closed(self) -> None:
        for processor in (
            StubProcessor(result=DownloadResult(path=Path("/tmp/42.mp4"), file_size=1)),
            StubProcessor(error=RuntimeError("boom")),
            StubProcessor(error=DownloadError("This video is not available")),
            StubProcessor(error=MediaValidationError("duration mismatch")),
        ):
            with self.subTest(processor=processor):
                spy = RecordingSpy()
                run_once(processor, spy)
                self.assertEqual(len(spy.started), len(spy.finished))


class DbRunRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.execute(text(SCHEMA))

    def rows(self) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text("SELECT * FROM video_download_runs")).mappings()]

    def test_open_row_stays_running_until_finished(self) -> None:
        recorder = DbRunRecorder(self.engine)
        run_id = recorder.start(JOB)

        row = self.rows()[0]
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["finished_at"])

        recorder.finish(
            run_id,
            status="ok",
            result=DownloadResult(path=Path("/tmp/42.mp4"), file_size=99, download_seconds=29.4, transcode_seconds=8.6),
        )

        row = self.rows()[0]
        self.assertEqual(row["status"], "ok")
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(row["download_seconds"], 29)
        self.assertEqual(row["transcode_seconds"], 9)
        self.assertEqual(row["file_size"], 99)

    def test_recording_failure_never_breaks_the_job(self) -> None:
        # Таблицы нет — учёт статистики обязан промолчать, а не уронить загрузку.
        broken = create_engine("sqlite://")
        recorder = DbRunRecorder(broken)

        self.assertIsNone(recorder.start(JOB))
        recorder.finish(None, status="ok")

    def test_null_recorder_is_inert(self) -> None:
        recorder = NullRunRecorder()
        self.assertIsNone(recorder.start(JOB))
        recorder.finish(None, status="ok")


class TruncateErrorTests(unittest.TestCase):
    def test_keeps_type_and_message(self) -> None:
        self.assertEqual(truncate_error(ValueError("boom")), "ValueError: boom")

    def test_truncates_long_messages(self) -> None:
        truncated = truncate_error(RuntimeError("x" * 5000))
        self.assertEqual(len(truncated), 2000)
        self.assertTrue(truncated.endswith("…"))

    def test_none_stays_none(self) -> None:
        self.assertIsNone(truncate_error(None))


if __name__ == "__main__":
    unittest.main()
