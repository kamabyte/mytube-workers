from __future__ import annotations

import logging
import urllib.error
import urllib.request

from video_downloader.domain.models import DownloadJob

LOGGER = logging.getLogger(__name__)

# Хук ждать долго незачем: уведомление — приятное дополнение, а не часть загрузки.
TIMEOUT_SECONDS = 5


class HttpDownloadNotifier:
    """Сообщает mytube-api, что видео скачалось: api тут же рассылает
    событие веб-клиентам через Reverb («Видео готово»).

    Адрес — шаблон с {id} (VIDEO_DOWNLOADER_NOTIFY_URL), токен — общий с api
    (WORKER_HOOK_TOKEN там). Любая ошибка логируется и проглатывается:
    не дошедшее уведомление не повод считать загрузку неудачной.
    """

    def __init__(self, url_template: str, token: str) -> None:
        self._url_template = url_template
        self._token = token

    def downloaded(self, job: DownloadJob) -> None:
        url = self._url_template.replace("{id}", str(job.id))
        request = urllib.request.Request(
            url,
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            LOGGER.warning("Could not notify api about a downloaded video. id=%s error=%s", job.id, error)


class NullDownloadNotifier:
    """Хук не настроен (локальные прогоны, dry-run)."""

    def downloaded(self, job: DownloadJob) -> None:
        return None
