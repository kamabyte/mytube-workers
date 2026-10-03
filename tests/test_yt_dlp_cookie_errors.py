import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from yt_dlp.utils import DownloadError

from video_downloader.config import resolve_existing_optional_path
from video_downloader.infrastructure.downloaders.yt_dlp import (
    translate_cookie_access_error,
)


class TranslateCookieAccessErrorTests(unittest.TestCase):
    def test_returns_none_without_cookie_configuration(self) -> None:
        result = translate_cookie_access_error(
            FileNotFoundError("missing"),
            yt_dlp_cookie_file=None,
            yt_dlp_cookies_from_browser=None,
        )

        self.assertIsNone(result)

    def test_translates_missing_cookie_file(self) -> None:
        result = translate_cookie_access_error(
            FileNotFoundError("missing"),
            yt_dlp_cookie_file=Path("/srv/mytube/youtube-cookies.txt"),
            yt_dlp_cookies_from_browser=None,
        )

        self.assertIsInstance(result, DownloadError)
        self.assertIn("VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE", str(result))
        self.assertIn("/srv/mytube/youtube-cookies.txt", str(result))

    def test_translates_missing_browser_cookie_store(self) -> None:
        result = translate_cookie_access_error(
            FileNotFoundError("missing"),
            yt_dlp_cookie_file=None,
            yt_dlp_cookies_from_browser=("chrome", "Default"),
        )

        self.assertIsInstance(result, DownloadError)
        self.assertIn("VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER=chrome", str(result))
        self.assertIn("profile: Default", str(result))

    def test_translates_cookie_permission_error(self) -> None:
        result = translate_cookie_access_error(
            PermissionError("denied"),
            yt_dlp_cookie_file=Path("/srv/mytube/youtube-cookies.txt"),
            yt_dlp_cookies_from_browser=None,
        )

        self.assertIsInstance(result, DownloadError)
        self.assertIn("permissions", str(result).lower())


class ResolveExistingOptionalPathTests(unittest.TestCase):
    def test_rejects_unreadable_cookie_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cookie_path = Path(temp_dir) / "cookies.txt"
            cookie_path.write_text("cookie data")
            cookie_path.chmod(0)

            try:
                result = resolve_existing_optional_path(
                    str(cookie_path),
                    base_dir=Path(temp_dir),
                )
            finally:
                cookie_path.chmod(0o600)

        if os.geteuid() == 0:
            self.skipTest("Root can read files regardless of permission bits")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
