from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from video_downloader.application.process_jobs import ProcessJobs
from video_downloader.domain.models import DownloadJob, DownloadResult
from video_downloader.infrastructure.notifiers.http_notifier import HttpDownloadNotifier

JOB = DownloadJob(id=42, external_id="ext42", name="Video", channel_id=7, duration_seconds=100)
RESULT = DownloadResult(path=Path("/tmp/42.mp4"), file_size=1, download_seconds=1, transcode_seconds=0)


class HookServer:
    """Настоящий HTTP-сервер на свободном порту — запоминает запросы."""

    def __init__(self, status: int = 204) -> None:
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                owner.requests.append({"path": self.path, "authorization": self.headers.get("Authorization")})
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args) -> None:
                return None

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/internal/videos/{{id}}/downloaded"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class HttpDownloadNotifierTests(unittest.TestCase):
    def test_posts_the_video_id_with_the_token(self) -> None:
        server = HookServer()
        self.addCleanup(server.close)

        HttpDownloadNotifier(server.url, "secret").downloaded(JOB)

        self.assertEqual(server.requests, [{"path": "/api/internal/videos/42/downloaded", "authorization": "Bearer secret"}])

    def test_an_error_response_never_breaks_the_download(self) -> None:
        server = HookServer(status=500)
        self.addCleanup(server.close)

        with self.assertLogs("video_downloader.infrastructure.notifiers.http_notifier", "WARNING"):
            HttpDownloadNotifier(server.url, "secret").downloaded(JOB)

    def test_an_unreachable_api_never_breaks_the_download(self) -> None:
        with self.assertLogs("video_downloader.infrastructure.notifiers.http_notifier", "WARNING"):
            HttpDownloadNotifier("http://127.0.0.1:9/api/internal/videos/{id}/downloaded", "secret").downloaded(JOB)


class Sink:
    def __init__(self, updated: bool) -> None:
        self.updated = updated

    def mark_success(self, job, result) -> bool:
        return self.updated

    def mark_failure(self, job, error) -> None:
        return None

    def mark_unavailable(self, job) -> bool:
        return True


class Recorder:
    def start(self, job) -> int | None:
        return None

    def finish(self, run_id, *, status, result=None, error=None) -> None:
        return None


class Processor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def process(self, job: DownloadJob) -> DownloadResult:
        if self.error:
            raise self.error
        return RESULT


class Spy:
    def __init__(self) -> None:
        self.notified: list[int] = []

    def downloaded(self, job: DownloadJob) -> None:
        self.notified.append(job.id)


def run(processor: Processor, sink: Sink, notifier: Spy) -> None:
    class Source:
        def fetch_pending(self, limit: int, exclude=()) -> list[DownloadJob]:
            return [JOB]

    ProcessJobs(
        source=Source(),
        sink=sink,
        processor=processor,
        recorder=Recorder(),
        poll_interval=1,
        batch_size=1,
        dry_run=False,
        notifier=notifier,
    ).run_once()


class ProcessJobsNotificationTests(unittest.TestCase):
    def test_notifies_after_the_video_is_marked_downloaded(self) -> None:
        spy = Spy()
        run(Processor(), Sink(updated=True), spy)
        self.assertEqual(spy.notified, [42])

    def test_stays_silent_when_nothing_was_marked(self) -> None:
        spy = Spy()
        run(Processor(), Sink(updated=False), spy)
        self.assertEqual(spy.notified, [])

    def test_stays_silent_when_the_download_failed(self) -> None:
        spy = Spy()
        with self.assertLogs("video_downloader.application.process_jobs", "ERROR"):
            run(Processor(error=RuntimeError("boom")), Sink(updated=True), spy)
        self.assertEqual(spy.notified, [])


if __name__ == "__main__":
    unittest.main()
