from __future__ import annotations

import unittest

from yt_dlp.utils import DownloadError

from video_downloader.downloader import (
    is_authentication_required_error,
    is_permanently_unavailable_error,
)


def error(message: str) -> DownloadError:
    return DownloadError(message)


class PermanentlyUnavailableTests(unittest.TestCase):
    def test_recognises_youtube_wording_for_a_removed_video(self) -> None:
        # Именно эта формулировка приходит от YouTube и именно её не ловил
        # старый список маркеров: подстрока "video unavailable" не находится
        # в "this video is not available".
        self.assertTrue(
            is_permanently_unavailable_error(error("ERROR: [youtube] p7JgOUHw6pE: Video unavailable"))
        )

    def test_recognises_all_known_permanent_wordings(self) -> None:
        for message in (
            "ERROR: [youtube] abc: Video unavailable",
            "ERROR: [youtube] abc: This video is unavailable",
            "ERROR: [youtube] abc: This video is not available",
            "ERROR: [youtube] abc: This content isn't available.",
            "ERROR: [youtube] abc: This video is no longer available because the uploader has closed their account",
            "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
            "ERROR: [youtube] abc: This video has been removed by the uploader",
            "ERROR: [youtube] abc: This video has been removed for violating YouTube's policy",
            "ERROR: [youtube] abc: This video is private",
            "ERROR: [youtube] abc: The uploader has not made this video available in your country",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_permanently_unavailable_error(error(message)))

    def test_ignores_errors_that_resolve_on_their_own(self) -> None:
        for message in (
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
            "WARNING: [youtube] abc: nsig extraction failed: Some formats may be missing",
            "ERROR: [youtube] abc: This live event will begin in 3 hours. Premieres in 3 hours",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "ERROR: Unable to download webpage: <urlopen error timed out>",
            "ERROR: [youtube] abc: n challenge solving failed",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_permanently_unavailable_error(error(message)))

    def test_authentication_wins_over_unavailability(self) -> None:
        # YouTube иногда отдаёт обе фразы разом. Списать такое видео как
        # снятое — значит потерять его навсегда из-за протухших cookies.
        combined = error(
            "ERROR: [youtube] abc: Video unavailable. Sign in to confirm you're not a bot"
        )
        self.assertFalse(is_permanently_unavailable_error(combined))
        self.assertTrue(is_authentication_required_error(combined))


if __name__ == "__main__":
    unittest.main()
