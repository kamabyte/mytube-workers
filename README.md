# Video Downloader

Небольшой воркер на `uv`: читает базу данных Laravel-приложения, скачивает ролики из таблицы `videos`, конвертирует их в MP4, выставляет `is_downloaded = true` и после каждой успешной загрузки сохраняет итоговый размер файла в байтах.

Если конвертация прошла успешно, файл сохраняется как `<videos.id>.mp4` в каталоге канала.

Воркер, API и база данных работают на одной машине. Нет ни очереди, ни удалённого транспорта, ни второго хоста.

## Что он делает

- по умолчанию берёт настройки базы данных из `workers/.env`
- выбирает из `videos` строки, где `is_downloaded = false`
- скачивает каждый ролик с YouTube через `yt-dlp`
- конвертирует скачанные ролики в MP4 с помощью `ffmpeg`
- после успешной загрузки выставляет `videos.is_downloaded` в `true`
- записывает в `videos.file_size` размер сконвертированного файла в байтах
- помечает навсегда недоступные на YouTube ролики как обработанные, чтобы не уходить в бесконечные повторы
- может отработать один раз или работать как периодически опрашивающий воркер

## Установка

```bash
cd ~/mytube/workers
uv sync
```

На машине, где запускается загрузчик, должен быть установлен `ffmpeg`.

Актуальным версиям `yt-dlp` для загрузки с YouTube нужен ещё и поддерживаемый JavaScript-рантайм, чтобы проходить проверки (challenge). Перед запуском загрузчика установите на хост воркера один из них:

```bash
curl -fsSL https://deno.land/install.sh | sh
```

или Node.js 20+ из пакетного менеджера либо с [nodejs.org](https://nodejs.org/).

Создайте `workers/.env` на основе `workers/.env.example`.

По умолчанию воркер берёт `workers/.env`, а если его ещё нет — откатывается на `api/.env`.

`uv sync` ставит `yt-dlp` с дополнениями по умолчанию, включая локальную поддержку `yt-dlp-ejs`.

Если YouTube начинает отвечать `Sign in to confirm you're not a bot`, задайте одну из этих настроек в `workers/.env` или в окружении оболочки:

- `VIDEO_DOWNLOADER_YTDLP_NODE_PATH=/usr/local/bin/node`
- `VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER=firefox`
- `VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER=chrome:Default`
- `VIDEO_DOWNLOADER_YTDLP_COOKIE_FILE=/absolute/path/to/youtube-cookies.txt`

`VIDEO_DOWNLOADER_YTDLP_NODE_PATH` передаётся в yt-dlp как явный путь к рантайму Node.js, так что воркер не зависит от `PATH` планировщика и от init-файлов оболочки.

`VIDEO_DOWNLOADER_YTDLP_COOKIES_FROM_BROWSER` задаётся в формате `browser[:profile[:keyring[:container]]]` и передаётся напрямую в `yt-dlp`.

Для транскодирования под Apple TV на слабых серверах ffmpeg настраивается необязательными параметрами в `workers/.env`:

- `VIDEO_DOWNLOADER_FFMPEG_PRESET=superfast`
- `VIDEO_DOWNLOADER_FFMPEG_CRF=24`
- `VIDEO_DOWNLOADER_FFMPEG_AUDIO_BITRATE=128k`
- `VIDEO_DOWNLOADER_FFMPEG_THREADS=1`

Видео и аудио обрабатываются независимо, поэтому файл, картинка которого уже
в H.264, платит только за перекодирование звука:

```bash
ffmpeg -y -i input_file \
  -c:v libx264 -preset superfast -crf 24 -pix_fmt yuv420p -threads 1 \
  -c:a aac -b:a 128k -ac 2 \
  -movflags +faststart \
  output.mp4
```

Здесь пропускная способность и низкое потребление памяти важнее точного соответствия исходнику. Если при переводе из AV1 в H.264 сохранить битрейт источника, качество картинки, как правило, упадёт.

## Архитектура

Воркер устроен как небольшое многослойное приложение:

- `entrypoints` разбирает аргументы CLI и собирает зависимости
- `application` содержит основной сценарий обработки
- `domain` содержит базовые модели и интерфейсы
- `infrastructure` содержит конкретные адаптеры для базы данных, yt-dlp и ffmpeg

Основная идея:

```text
job source -> processing pipeline -> result sink
```

Текущие адаптеры:

- `DbJobSource` читает ожидающие строки из таблицы `videos`
- `DbResultSink` записывает результат обратно в ту же таблицу
- `DbRunRecorder` пишет каждую попытку в `video_download_runs` (в режиме `--dry-run` его заменяет `NullRunRecorder`)
- `YtDlpProcessor` скачивает и конвертирует одно задание
- вспомогательные функции ffmpeg и проверки совместимости медиа

У `JobSource` и `ResultSink` по одной боевой реализации. Интерфейсами они оставлены для того, чтобы `ProcessJobs` не зависел от хранилища и в тестах его можно было гонять на фейках.

### Структура каталогов

Текущая раскладка пакета:

```text
workers/
  README.md
  pyproject.toml
  scripts/
    test-local-download.sh
  src/video_downloader/
    __main__.py
    config.py
    downloader.py
    application/
      process_jobs.py
    domain/
      models.py
      ports.py
    entrypoints/
      cli.py
    infrastructure/
      converters/
        ffmpeg.py
      downloaders/
        yt_dlp.py
      persistence/
        db.py
      processors/
        yt_dlp_processor.py
      recorders/
        db_recorder.py
      sinks/
        db_sink.py
      sources/
        db_source.py
```

### Карта файлов

Ключевые файлы и их обязанности:

- `src/video_downloader/__main__.py`
  Точка входа модуля Python для `python -m video_downloader`

- `src/video_downloader/config.py`
  Загружает окружение, разрешает пути и собирает конфигурацию времени выполнения

- `src/video_downloader/domain/models.py`
  Базовые модели, например `DownloadJob` и `DownloadResult`

- `src/video_downloader/domain/ports.py`
  Интерфейсы `JobSource`, `ResultSink`, `JobProcessor` и `RunRecorder`

- `src/video_downloader/application/process_jobs.py`
  Основной сценарий. Выбирает задания, обрабатывает их и отмечает успех или неудачу.

- `src/video_downloader/entrypoints/cli.py`
  Разбирает команды `once`, `worker` и `test-url` и связывает прикладной сервис

- `src/video_downloader/infrastructure/sources/db_source.py`
  Читает ожидающие задания из таблицы `videos`

- `src/video_downloader/infrastructure/sinks/db_sink.py`
  Помечает ролики как скачанные и сохраняет `file_size`

- `src/video_downloader/infrastructure/recorders/db_recorder.py`
  Записывает попытки загрузки в `video_download_runs` (см. «Статистика загрузок»)

- `src/video_downloader/infrastructure/persistence/db.py`
  Создаёт движок SQLAlchemy

- `src/video_downloader/infrastructure/processors/yt_dlp_processor.py`
  Инфраструктурный процессор, которым пользуется прикладной слой. Вызывает загрузчик и возвращает `DownloadResult`

- `src/video_downloader/infrastructure/downloaders/yt_dlp.py`
  Низкоуровневый адаптер yt-dlp и логика определения рантайма

- `src/video_downloader/infrastructure/converters/ffmpeg.py`
  ffmpeg и вспомогательные функции проверки совместимости

- `src/video_downloader/downloader.py`
  Оркестрация загрузки одного ролика: повторное использование уже скачанных файлов, работа с архивом, повтор при HTTP 416, нормализация путей и субтитров, remux или транскодирование, а также классификаторы ошибок, которыми пользуется `ProcessJobs`

### Ход обработки

```text
CLI
  -> load config
  -> create DbJobSource
  -> create YtDlpProcessor
  -> create DbResultSink
  -> ProcessJobs.run_once() or ProcessJobs.run_worker()
```

Внутри `ProcessJobs` порядок такой:

```text
fetch jobs -> process one job -> mark success/failure
```

### Точки входа

Проект запускается через скрипты из `pyproject.toml`:

```toml
[project.scripts]
video-downloader = "video_downloader.entrypoints.cli:main"
workers = "video_downloader.entrypoints.cli:main"
```

На практике это значит:

- `uv run video-downloader ...` запускает воркер
- `uv run workers ...` — псевдоним той же точки входа
- `python -m video_downloader ...` тоже работает, через `__main__.py`

Поддерживаемые команды CLI:

- `once`
  Обработать одну пачку и завершиться

- `worker`
  Обрабатывать задания в цикле опроса

- `test-url`
  Скачать одну ссылку YouTube локально через тот же конвейер yt-dlp/remux/транскодирование/субтитры, не трогая базу данных

### Почему разделено именно так

Такое разделение отделяет работу с хранилищем от обработки:

- `ProcessJobs` отвечает за цикл воркера и политику обработки ошибок
- адаптеры источника и приёмника владеют всеми SQL-запросами
- процессор владеет всем, что касается yt-dlp и ffmpeg

Благодаря этому сценарий можно тестировать без базы данных и без доступа к сети, а SQL не попадает в конвейер загрузки.

## Использование

Один проход:

```bash
uv run video-downloader once
```

Непрерывная работа с интервалом 5 минут:

```bash
uv run video-downloader worker --interval 300
```

Одна локальная тестовая загрузка по любой ссылке YouTube:

```bash
uv run video-downloader test-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Если вы уже скопировали в этот проект `.env` боевого воркера, `test-url` возьмёт оттуда те же cookies, языки субтитров, параметры ffmpeg и каталог загрузок. Есть и скрипт-обёртка, который запускает ту же команду через локальное виртуальное окружение:

```bash
bash scripts/test-local-download.sh "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Полезные флаги:

- `--limit 10` ограничивает число ожидающих роликов, выбираемых за цикл
- `--download-dir /path/to/videos` меняет каталог назначения
- `--env-file /path/to/.env` указывает на другой env-файл воркера
- `--dry-run` проверяет чтение из БД без загрузки и без обновления строк
- `--transcode-for-apple-tv` включает запасное транскодирование ffmpeg в `h264+aac+mp4`
- `test-url --channel-id 999` задаёт подкаталог для результата локального теста
- `test-url --video-id 123456` задаёт числовое имя выходного файла вместо стабильного хеша по умолчанию

Если ни `VIDEO_DOWNLOAD_DIR`, ни `--download-dir` не заданы, загрузки сохраняются в каталог API, соседний с каталогом воркера:

```text
../api/storage/app/public/videos
```

### Remux или транскодирование

По умолчанию загрузчик экономен:

- скачивает выбранные потоки
- по возможности делает remux/слияние в MP4
- не запускает принудительное полное транскодирование ffmpeg

Так нагрузка на CPU остаётся низкой.

Если нужна более надёжная совместимость с устройствами Apple, включите транскодирование явно:

```bash
uv run video-downloader once --transcode-for-apple-tv
```

или задайте его в `workers/.env`:

```dotenv
VIDEO_DOWNLOADER_TRANSCODE_FOR_APPLE_TV=true
```

Когда оно включено, воркер:

- сначала скачивает и делает remux
- проверяет, совместим ли получившийся файл с устройствами Apple
- и только после этого транскодирует несовместимые файлы в `h264 + aac + mp4`

«Совместимый» означает контейнер MP4/MOV, видео H.264 или HEVC и **стерео AAC**
в звуке. AC-3, E-AC-3, Opus и всё, где больше двух каналов, перекодируется,
даже если картинка копируется без изменений: YouTube отдаёт Dolby Digital Plus
5.1 в контейнере `m4a` (itag 328), и такая дорожка доходит до tvOS как видео
без звука.

### Выбор исходного формата

Потоки выбираются сначала по разрешению (не выше 1080p), а среди вариантов
одного размера побеждает видео H.264 со звуком AAC. Это ровно то, что
декодирует AVPlayer, так что на практике вообще ничего не транскодируется.

Фильтр по контейнеру здесь не работает: YouTube отдаёт `av01` тоже в `mp4`,
и по качеству он стоит выше `avc1`, поэтому `bestvideo*[ext=mp4]` всегда
выбирал AV1 — а воркер потом перекодировал его в H.264, тратя CPU на файл
примерно вдвое больше, чем собственный `avc1` от YouTube. У звука зеркальная
проблема: Dolby Digital Plus (itag 328, `ec-3`, 5.1) тоже приходит как `m4a`,
причём с битрейтом выше, чем у обычного AAC.

Разрешение всё равно в приоритете, поэтому ролик, у которого H.264 есть только
до 480p, а VP9 — до 1080p, скачивается в VP9 и транскодируется. Качество
никогда не приносится в жертву более дешёвому конвейеру.

### Целостность загрузки

Прежде чем пометить строку как скачанную, воркер проверяет готовый файл и
отбраковывает его, если в нём нет видеопотока, нет аудиопотока или
длительность расходится с `videos.duration_seconds` больше чем на 2% (но не
меньше чем на 5 с). Отбракованный файл удаляется вместе со своей записью в
`download_archive`, поэтому следующий цикл скачивает его заново, а не отдаёт
вечно обрезанное или беззвучное видео.

### Ролики, которые YouTube больше не отдаёт

Удалённый, приватный или заблокированный в регионе ролик получает
`videos.is_unavailable` и навсегда покидает очередь; попытка записывается как
`unavailable`, а не `failed`, потому что это не вина воркера и в статистике
ошибок ей не место.

Флаг независим от `is_downloaded` и намеренно не влияет на то, что отдаёт
API: файл, скачанный до исчезновения ролика с YouTube, продолжает
проигрываться. В этом и смысл архива.

Распознавание идёт по сообщению yt-dlp, и формулировка важна — YouTube
отвечает «Video unavailable», а подстрока `this video is not available` этот
случай не покрывает. Ошибиться здесь не безобидно: ролик 2771 набрал 32
неудачные попытки за три часа, возвращаясь в очередь каждый цикл. Ошибки
аутентификации проверяются первыми, поэтому ролик, скрытый за протухшими
cookies, никогда не списывается как исчезнувший.

## Статистика загрузок

Каждая попытка скачать ролик записывается в `video_download_runs` — одна
строка на запуск: она открывается, когда воркер берёт задание, и закрывается,
когда он его отпускает:

| столбец | значение |
| --- | --- |
| `status` | `running` / `ok` / `failed` / `unavailable` |
| `download_seconds` | время на скачивание потоков |
| `transcode_seconds` | время в ffmpeg |
| `file_size` | байты на диске, если запуск успешен |
| `error` | тип и сообщение, обрезанные до 2000 символов |

`videos.downloaded_at` говорит лишь о том, когда всё закончилось, а journald
хранит логи воркера меньше двух суток, так что ни то ни другое не отвечает на
вопрос «сколько обычно длится загрузка». Две фазы хранятся раздельно
намеренно: по первым замерам на ffmpeg приходилось 92% реального времени, а
это не видно, если записывать только итог.

Строка, оставшаяся в `running`, — не баг: это запуск, воркер которого умер,
не успев её закрыть, и именно такие случаи и нужно уметь считать.

```sql
-- за неделю: сколько попыток, сколько удачных, на что уходило время
select count(*)                          as attempts,
       sum(status = 'ok')                as succeeded,
       round(avg(download_seconds), 1)   as avg_download,
       round(avg(transcode_seconds), 1)  as avg_ffmpeg
from video_download_runs
where started_at > date('now', '-7 day');

-- что падает чаще всего
select video_id, count(*) as attempts, max(error) as last_error
from video_download_runs
where status = 'failed'
group by video_id
order by attempts desc;

-- самые долгие загрузки
select r.video_id, v.name, r.download_seconds, r.transcode_seconds
from video_download_runs r join videos v on v.id = r.video_id
where r.status = 'ok'
order by r.download_seconds + r.transcode_seconds desc
limit 10;
```

Запись статистики никогда не роняет задание: если таблицы нет или запись
завершилась ошибкой, регистратор пишет предупреждение в лог, а загрузка
продолжается. Потерять строку статистики дёшево, потерять ролик — нет.

## Тесты

```bash
uv run pytest
```

## Лицензия

[MIT](LICENSE)
