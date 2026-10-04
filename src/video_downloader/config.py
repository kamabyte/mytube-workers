from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from video_downloader.infrastructure.converters.ffmpeg import FFmpegTranscodeOptions

@dataclass(slots=True)
class AppConfig:
    workers_root: Path
    api_root: Path
    env_file: Path
    db_url: str
    download_dir: Path
    archive_file: Path
    poll_interval: int
    batch_size: int
    transcode_for_apple_tv: bool
    transcode_options: FFmpegTranscodeOptions
    log_level: str
    yt_dlp_cookie_file: Path | None
    yt_dlp_cookies_from_browser: tuple[str, ...] | None
    yt_dlp_node_path: Path | None
    subtitle_langs: tuple[str, ...]
    # Хук «видео скачалось» в mytube-api: шаблон адреса с {id} и токен.
    notify_url: str | None = None
    notify_token: str | None = None


def load_config(
    *,
    env_file: Path | None = None,
    download_dir: Path | None = None,
    poll_interval: int = 300,
    batch_size: int = 100,
) -> AppConfig:
    repo_root = Path(__file__).resolve().parents[3]
    workers_root = repo_root / "workers"
    api_root = repo_root / "api"
    default_env_file = resolve_default_env_file(
        explicit_env_file=env_file,
        workers_root=workers_root,
        api_root=api_root,
    )
    resolved_env_file = default_env_file.resolve()

    if not resolved_env_file.exists():
        raise FileNotFoundError(f"Worker env file not found: {resolved_env_file}")

    env = {
        **dotenv_values(resolved_env_file),
        **os.environ,
    }

    configured_download_dir = download_dir or resolve_optional_path(
        env.get("VIDEO_DOWNLOAD_DIR"),
        base_dir=resolved_env_file.parent,
    )
    resolved_download_dir = (configured_download_dir or (api_root / "storage" / "app" / "public" / "videos")).resolve()
    resolved_download_dir.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        workers_root=workers_root,
        api_root=api_root,
        env_file=resolved_env_file,
        db_url=build_database_url(env, api_root),
        download_dir=resolved_download_dir,
        archive_file=resolved_download_dir / ".downloaded-archive.txt",
        poll_interval=poll_interval,
        batch_size=batch_size,
        transcode_for_apple_tv=str(env.get("VIDEO_DOWNLOADER_TRANSCODE_FOR_APPLE_TV", "false")).lower() in {"1", "true", "yes", "on"},
        transcode_options=FFmpegTranscodeOptions(
            preset=str(env.get("VIDEO_DOWNLOADER_FFMPEG_PRESET", "superfast")).strip() or "superfast",
            crf=parse_int_env(env.get("VIDEO_DOWNLOADER_FFMPEG_CRF"), default=24),
            audio_bitrate=str(env.get("VIDEO_DOWNLOADER_FFMPEG_AUDIO_BITRATE", "128k")).strip() or "128k",
            threads=max(1, parse_int_env(env.get("VIDEO_DOWNLOADER_FFMPEG_THREADS"), default=1)),
        ),
        log_level=str(env.get("VIDEO_DOWNLOADER_LOG_LEVEL", "INFO")).upper(),
        yt_dlp_cookie_file=resolve_existing_optional_path(
            env.get("VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE"),
            base_dir=resolved_env_file.parent,
        ),
        yt_dlp_cookies_from_browser=parse_browser_spec(
            env.get("VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER"),
        ),
        yt_dlp_node_path=resolve_existing_optional_path(
            env.get("VIDEO_DOWNLOADER_YTDLP_NODE_PATH"),
            base_dir=resolved_env_file.parent,
            env_var_name="VIDEO_DOWNLOADER_YTDLP_NODE_PATH",
        ),
        subtitle_langs=parse_subtitle_langs(
            env.get("VIDEO_DOWNLOADER_SUBTITLE_LANGS"),
            default=("en", "ru"),
        ),
        notify_url=str(env.get("VIDEO_DOWNLOADER_NOTIFY_URL") or "").strip() or None,
        notify_token=str(env.get("VIDEO_DOWNLOADER_NOTIFY_TOKEN") or "").strip() or None,
    )


def resolve_default_env_file(
    *,
    explicit_env_file: Path | None,
    workers_root: Path,
    api_root: Path,
) -> Path:
    if explicit_env_file is not None:
        return explicit_env_file

    workers_env_file = workers_root / ".env"
    if workers_env_file.exists():
        return workers_env_file

    api_env_file = api_root / ".env"
    if api_env_file.exists():
        return api_env_file

    return workers_env_file


def build_database_url(env: dict[str, str | None], api_root: Path) -> str:
    db_connection = str(env.get("DB_CONNECTION") or "sqlite").lower()

    if db_connection == "sqlite":
        raw_database = env.get("DB_DATABASE") or str(api_root / "database" / "database.sqlite")
        database_path = Path(raw_database)
        if not database_path.is_absolute():
            database_path = (api_root / database_path).resolve()
        return f"sqlite:///{database_path}"

    username = env.get("DB_USERNAME") or ""
    password = env.get("DB_PASSWORD") or ""
    host = env.get("DB_HOST") or "127.0.0.1"
    port = env.get("DB_PORT") or default_port_for(db_connection)
    database = env.get("DB_DATABASE") or ""

    auth = username
    if password:
        auth = f"{auth}:{password}"

    driver = {
        "mysql": "mysql+pymysql",
        "mariadb": "mysql+pymysql",
        "pgsql": "postgresql+psycopg",
    }.get(db_connection)

    if driver is None:
        raise ValueError(f"Unsupported DB_CONNECTION: {db_connection}")

    return f"{driver}://{auth}@{host}:{port}/{database}"


def default_port_for(db_connection: str) -> str:
    return {
        "mysql": "3306",
        "mariadb": "3306",
        "pgsql": "5432",
    }[db_connection]


def resolve_existing_optional_path(
    raw_path: str | None,
    *,
    base_dir: Path,
    env_var_name: str = "VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE",
) -> Path | None:
    if not raw_path:
        return None

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()

    if not path.exists():
        warnings.warn(
            f"{env_var_name} points to a missing file and will be ignored: {path}",
            stacklevel=2,
        )
        return None

    if not path.is_file():
        warnings.warn(
            f"{env_var_name} does not point to a regular file and will be ignored: {path}",
            stacklevel=2,
        )
        return None

    if not os.access(path, os.R_OK):
        warnings.warn(
            f"{env_var_name} points to an unreadable file and will be ignored: {path}",
            stacklevel=2,
        )
        return None

    return path


def resolve_optional_path(raw_path: str | None, *, base_dir: Path) -> Path | None:
    if not raw_path:
        return None

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()

    return path


def parse_browser_spec(raw_spec: str | None) -> tuple[str, ...] | None:
    if not raw_spec:
        return None

    parts = tuple(part.strip() for part in raw_spec.split(":"))
    normalized = tuple(part for part in parts if part)
    return normalized or None


def parse_subtitle_langs(raw_value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw_value is None:
        return default

    parts = (part.strip() for part in raw_value.split(","))
    return tuple(part for part in parts if part)


def parse_int_env(raw_value: str | None, *, default: int) -> int:
    if raw_value is None:
        return default

    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default
