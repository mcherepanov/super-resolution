# План: Web GUI для super-resolution

## Архитектура

```
Browser → web (FastAPI + HTMX)
            ↓ upload → input/
            ↓ POST /process | /process-cue
          SQLite (data/app.db)
            ↓ publish {job_id}
          RabbitMQ (sr_jobs)
            ↓ consume
          worker (flashsr GPU | worker-mock)
            ↓ job_type dispatch
          process      → ffmpeg-цепочка → [FlashSR] → output/
          cue_split    → нарезка по CUE → input/<album>/
          cue_batch    → пайплайн на каждый FILE из CUE
```

Single-user. Журнал задач в SQLite. Повторная постановка в очередь — по паре `input_path` + `output_path` (не блокируется завершённый job).

**Режимы:**
- **Только обработка** — `worker-mock`, ffmpeg-фильтры и конвертация (`ENHANCE_AVAILABLE=0`)
- **С AI-улучшением** — `flashsr` GPU, FlashSR как завершающий этап (`ENHANCE_AVAILABLE=1`)

---

## Этап 1 — MVP ✅

### Инфраструктура
- [x] `rabbitmq` в compose (management UI :15672)
- [x] `web` контейнер (FastAPI, :8080)
- [x] `flashsr` — worker-режим (`scripts/worker.py`)
- [x] общий volume `data/` для SQLite
- [x] `INPUT_DIR` / `OUTPUT_DIR` как сейчас

### База данных (SQLite)
- [x] `scripts/db.py` — схема, CRUD
- [x] таблица `jobs`: filename, paths, status, даты, error, duration, sample rates
- [x] статусы: `queued` | `processing` | `done` | `failed` | `skipped`
- [x] поля `options` (JSON), `output_format`, `job_type`

### Worker
- [x] `scripts/worker.py` — consumer RabbitMQ, prefetch=1
- [x] модель грузится один раз при старте (prod)
- [x] cleanup temp-файлов при ошибке
- [x] обновление статусов в SQLite

### Web UI (базовый)
- [x] загрузка файлов → `input/` (без автоматической очереди)
- [x] таблица истории/очереди (HTMX poll каждые 3 с)
- [x] опциональный пароль `APP_PASSWORD` (HTTP Basic)
- [x] Material Design 3 стили

### Mock-режим (локальный тест UI)
- [x] `MOCK_MODE=1` в `.env`
- [x] `worker-mock` контейнер (без GPU, без FlashSR)
- [x] баннер «Только обработка — AI недоступен»
- [x] `make clone` заблокирован при MOCK_MODE

### CLI / обработка аудио
- [x] `scripts/super_resolve.py` — ручной запуск без GUI
- [x] симметричный overlap-add (sin²/cos²)
- [x] выход FlashSR: 48 kHz → 44.1 kHz

### Makefile
- [x] `start` / `stop` / `status` / `logs`
- [x] `clone` — веса с HuggingFace
- [x] `admin` — sqlite-web для БД

### Git и секреты
- [x] репозиторий на [GitVerse](https://gitverse.ru/Max_Cherep/super-resolution)
- [x] `.gitignore` (`.env`, веса ~3 ГБ, аудио, `data/app.db`)
- [x] `make encode` / `make decode` — ansible-vault (`.env` ↔ `.env.vault`)

### Документация
- [x] README — Web UI, MOCK_MODE, vault, clone
- [x] `.env.example`

---

## Этап 6 — FFmpeg-обработка и CUE ✅

### Пайплайн обработки (`process`)
- [x] `scripts/ffmpeg_ops.py` — decode 48 kHz, фильтры, экспорт
- [x] `scripts/audio_pipeline.py` — цепочка обработки
- [x] `scripts/process_options.py` — парсинг/валидация опций из формы
- [x] фиксированный порядок: шум → EQ → compand → loudnorm → **AI (опция)** → экспорт
- [x] промежуточный WAV 48 kHz
- [x] чекбокс «Преобразовать в 44.1» (по умолчанию вкл.)
- [x] форматы выхода: wav, flac, mp3, m4a
- [x] вход: wav, flac, mp3, ogg, opus, ape, m4a (APE — только вход)
- [x] AI-улучшение только при `ENHANCE_AVAILABLE=1`
- [x] только конвертация / ресемпл — допустимый job
- [x] повторная очередь: блокировка только `queued`/`processing` на ту же пару вход→выход
- [x] перезапись существующего output (без skip)

### Фильтры (UI блоками, внутри блока — один выбор)
- [x] шум: afftdn (слайдеры nr/nf) | anlmdn
- [x] частоты: highpass | lowpass | оба (поля Hz)
- [x] динамика: compand (слайдер интенсивности)
- [x] нормализация: loudnorm
- [x] исправлен синтаксис compand для ffmpeg 4.x

### CUE sheet
- [x] `scripts/cue_sheet.py` — парсинг, валидация FILE
- [x] `scripts/cue_split.py` — нарезка ffmpeg по INDEX
- [x] upload `.cue` — ошибка если нет аудиофайлов из FILE
- [x] toast 5 с при загрузке CUE (`sessionStorage`, один раз за сессию)
- [x] режим **сплит** → `input/<stem>/` (wav/flac/mp3)
- [x] режим **образ целиком** → пайплайн `process`
- [x] **несколько FILE** → `cue_batch`, общий пайплайн на каждый файл
- [x] `job_type`: `process` | `cue_split` | `cue_batch`

### Скачивание и очистка
- [x] `scripts/download_utils.py`
- [x] скачивание: файл | ZIP (cue_split, cue_batch)
- [x] чекбокс «удалить» (по умолчанию вкл.) — файлы/папка + `DELETE` job из SQLite
- [x] `POST /download/{id}`

### Админка БД
- [x] `db-admin` (coleifer/sqlite-web) — профиль `admin`, `make admin`
- [x] http://localhost:8081 — без логина, `data/app.db`

---

## Этап 2 — Live progress (не реализован)

- [ ] WebSocket или SSE endpoint
- [ ] worker пишет прогресс чанков (опционально в SQLite или pub/sub)
- [ ] полоска прогресса на активной задаче
- [ ] для batch/CUE: «трек 3/12»

---

## Этап 7 — Анализ спектра до очереди (не реализован)

- [ ] `GET /analyze/{file}` после upload (ffprobe + сэмпл 10–15 с)
- [ ] подсказки в UI: highpass / denoise
- [ ] не блокировать кнопку «В очередь»
- [ ] ffmpeg в web-контейнере или parse на клиенте

---

## Этап 3 — Улучшения (частично / не реализован)

### Пайплайн и качество звука
- [ ] ffmpeg: `-af aresample=resampler=soxr` (вместо дефолтного ресемпла)
- [ ] `np.clip` перед записью WAV
- [ ] единый ресемплер на входе/выходе FlashSR
- [ ] `OVERLAP` / `WINDOW_LEN` в CLI
- [x] lowpass перед AI в Web UI (`enhance_lowpass`, только при `ENHANCE_AVAILABLE`)

### Производительность
- [ ] `torch.compile(model)` — проверить на GPU
- [ ] стерео: batch или параллельные CUDA streams
- [ ] выровнять CUDA в Docker (образ 12.2 / PyTorch cu118 → cu121/cu122)

### UI и инфраструктура
- [ ] удаление задач / очистка истории из UI (кроме скачивания с «удалить»)
- [ ] перетаскивание порядка блоков фильтров
- [ ] Celery вместо raw consumer (если устанет поддерживать worker)
- [ ] сузить `warnings.filterwarnings("ignore")`

### CUE (доработки)
- [ ] pregap, встроенный CUE во FLAC
- [ ] тесты на «грязных» cue (CP1251, несколько FILE со сплитом)

### Эксперименты (по желанию)
- [ ] постобработка: лимитер, shelf-EQ выше 16–18 kHz
- [ ] бенчмарк: лог RTF / peak в JSON

### Заметки по `--lowpass`
По умолчанию выключен — правильно для полнополосной музыки. Включать только для узкополосного входа.

---

## Этап 4 — Мультиюзер (не планируется)

- [ ] таблица `users`, auth, изоляция каталогов

---

## Этап 5 — GitVerse CI/CD (не реализован)

Платформа: [gitverse.ru](https://gitverse.ru)

### План интеграции
- [ ] `.gitverse/workflows/deploy.yml` — деплой по push в `master`
- [ ] self-hosted runner на GPU-сервере
- [ ] шаги: `git pull` → `make decode` → `make clone` → `make build` → `make up`
- [ ] secrets в GitVerse CI для vault-пароля
- [ ] (опционально) workflow lint/smoke в MOCK_MODE на облачном раннере

---

## Запуск

```bash
git pull
make decode              # .env из .env.vault
make clone               # веса (MOCK_MODE=0)
make build && make up    # или --profile mock
# http://localhost:8080
make admin               # sqlite-web → http://localhost:8081
```

| Сервис | URL |
|--------|-----|
| Web UI | http://localhost:8080 |
| RabbitMQ | http://localhost:15672 (guest/guest) |
| БД (sqlite-web) | http://localhost:8081 (`make admin`) |
