# Воркер MyTube: скачивает ролики через yt-dlp, при необходимости
# транскодирует ffmpeg'ом под Apple TV и отмечает результат в базе SQLite
# приложения. Образ ghcr.io/kamabyte/mytube-workers собирает GitHub Actions
# (.github/workflows/image.yml), запускает Dokploy вместе с mytube-api
# (compose.yml в репозитории mytube-api, каталог deploy/).
#
# Настройки — только переменные окружения (см. README, «Setup»). Файл
# --env-file пустой: он нужен лишь потому, что load_config требует
# существующий файл; значения из окружения его перекрывают.

# --- Сборка зависимостей ---------------------------------------------------
FROM python:3.12-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# --- Рабочий образ ---------------------------------------------------------
FROM python:3.12-slim-trixie
# ffmpeg/ffprobe — remux и транскодирование; nodejs — JS-рантайм, без
# которого yt-dlp не проходит проверки YouTube (EJS); ca-certificates —
# HTTPS к YouTube и GitHub (оттуда yt-dlp берёт компоненты EJS).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# uid/gid пользователя mytube на хосте (994:980): база и файлы в /srv/mytube/data
# принадлежат ему. Если медиатека принадлежит другой группе, её добавляет
# compose через group_add (MEDIA_GID в mytube-api/deploy/compose.yml).
RUN groupadd --system --gid 980 mytube \
 && useradd --system --uid 994 --gid mytube --home-dir /worker --no-create-home mytube
WORKDIR /app
COPY --from=build /app /app
RUN : > /app/container.env
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    HOME=/worker \
    VIDEO_DOWNLOADER_YTDLP_NODE_PATH=/usr/bin/node
USER mytube
# Воркер крутит цикл «взять задания → скачать → подождать interval».
# Остановка по SIGTERM прерывает текущую загрузку; незавершённое
# докачается в следующем цикле (is_downloaded ставится только в конце).
# 15 секунд: видео, которое попросили скачать из веба, должно уходить
# в работу сразу; пустой опрос очереди — один дешёвый запрос к SQLite.
CMD ["video-downloader", "worker", "--env-file", "/app/container.env", "--interval", "15"]
