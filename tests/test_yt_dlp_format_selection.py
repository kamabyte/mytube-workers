from __future__ import annotations

import unittest

from yt_dlp import YoutubeDL

from video_downloader.infrastructure.downloaders.yt_dlp import (
    FORMAT_SORT,
    YOUTUBE_FORMAT_SELECTOR,
)


def video_format(format_id: str, vcodec: str, height: int, tbr: int) -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": "mp4" if vcodec.startswith(("avc1", "av01")) else "webm",
        "protocol": "https",
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": height * 16 // 9,
        "tbr": tbr,
        "vbr": tbr,
    }


def audio_format(format_id: str, acodec: str, abr: int) -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": "m4a" if acodec.startswith(("mp4a", "ec-3")) else "webm",
        "protocol": "https",
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
    }


def select(formats: list[dict]) -> tuple[str, str]:
    """Прогоняет форматы через боевой селектор и возвращает (vcodec, acodec)."""
    ydl = YoutubeDL(
        {
            "format": YOUTUBE_FORMAT_SELECTOR,
            "format_sort": list(FORMAT_SORT),
            "simulate": True,
            "quiet": True,
            "no_warnings": True,
        }
    )
    result = ydl.process_video_result(
        {
            "_type": "video",
            "id": "test",
            "title": "test",
            "extractor": "test",
            "extractor_key": "Test",
            "webpage_url": "https://example.invalid/test",
            "formats": list(formats),
        },
        download=False,
    )
    chosen = result.get("requested_formats") or [result]
    video = next(f for f in chosen if f.get("vcodec") not in (None, "none"))
    audio = next(
        (f for f in chosen if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")),
        chosen[0],
    )
    return str(video["vcodec"]), str(audio["acodec"])


AAC = audio_format("140", "mp4a.40.2", 129)
# YouTube отдаёт Dolby Digital Plus тоже в m4a и с большим битрейтом.
EAC3 = audio_format("328", "ec-3", 384)
OPUS = audio_format("251", "opus", 119)


class FormatSelectionTests(unittest.TestCase):
    def test_prefers_h264_over_av1_at_the_same_resolution(self) -> None:
        # Главная регрессия: "[ext=mp4]" матчил av01, тот выигрывал по
        # качеству, и воркер потом перекодировал его в H.264 — файл на диске
        # выходил вдвое больше ютубовского же avc1.
        vcodec, _ = select([
            video_format("399", "av01.0.08M.08", 1080, 1290),
            video_format("248", "vp9", 1080, 1483),
            video_format("137", "avc1.640028", 1080, 2925),
            AAC,
        ])
        self.assertTrue(vcodec.startswith("avc1"), vcodec)

    def test_keeps_resolution_when_h264_lags_behind(self) -> None:
        # У части старых роликов H.264 есть только в 480p. Проседать до него
        # с 1080p нельзя — лучше перекодировать.
        vcodec, _ = select([
            video_format("135", "avc1.4d401e", 480, 1100),
            video_format("248", "vp9", 1080, 1483),
            AAC,
        ])
        self.assertEqual(vcodec, "vp9")

    def test_prefers_h264_at_the_best_resolution_it_reaches(self) -> None:
        vcodec, _ = select([
            video_format("135", "avc1.4d401e", 480, 1100),
            video_format("137", "avc1.640028", 1080, 2925),
            video_format("399", "av01.0.08M.08", 1080, 1290),
            AAC,
        ])
        self.assertTrue(vcodec.startswith("avc1"), vcodec)

    def test_never_picks_dolby_digital_plus_over_aac(self) -> None:
        _, acodec = select([video_format("137", "avc1.640028", 1080, 2925), EAC3, AAC])
        self.assertTrue(acodec.startswith("mp4a"), acodec)

    def test_never_picks_opus_over_aac(self) -> None:
        _, acodec = select([video_format("137", "avc1.640028", 1080, 2925), OPUS, AAC])
        self.assertTrue(acodec.startswith("mp4a"), acodec)

    def test_falls_back_to_opus_when_there_is_no_aac(self) -> None:
        _, acodec = select([video_format("137", "avc1.640028", 1080, 2925), OPUS])
        self.assertEqual(acodec, "opus")

    def test_caps_resolution_at_1080p(self) -> None:
        vcodec, _ = select([
            video_format("137", "avc1.640028", 1080, 2925),
            video_format("401", "av01.0.12M.08", 2160, 8000),
            AAC,
        ])
        self.assertTrue(vcodec.startswith("avc1"), vcodec)


if __name__ == "__main__":
    unittest.main()
