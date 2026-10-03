from __future__ import annotations

import unittest
from pathlib import Path

from video_downloader.infrastructure.downloaders.yt_dlp import (
    build_subtitle_language_patterns,
    build_yt_dlp_options,
)


class SubtitleLanguageMatchingTests(unittest.TestCase):
    def test_expands_plain_language_codes_to_match_variants(self) -> None:
        self.assertEqual(
            build_subtitle_language_patterns(("en", "ru")),
            ["^en(?:$|[-_].*)", "^ru(?:$|[-_].*)"],
        )

    def test_preserves_explicit_regex_patterns(self) -> None:
        self.assertEqual(
            build_subtitle_language_patterns(("en.*", "ru")),
            ["en.*", "^ru(?:$|[-_].*)"],
        )

    def test_build_options_uses_expanded_subtitle_patterns(self) -> None:
        options = build_yt_dlp_options(
            output_template="/tmp/%(id)s.%(ext)s",
            archive_file=Path("/tmp/archive.txt"),
            subtitle_langs=("en", "ru"),
        )

        self.assertEqual(
            options["subtitleslangs"],
            ["^en(?:$|[-_].*)", "^ru(?:$|[-_].*)"],
        )
        self.assertEqual(
            options["extractor_args"],
            {"youtube": {"skip": ["translated_subs"]}},
        )


if __name__ == "__main__":
    unittest.main()
