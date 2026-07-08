# План: Web GUI для super-resolution

## Архитектура

```
Browser → web (FastAPI + HTMX)
            ↓ upload, INSERT jobs
          SQLite (data/app.db)
            ↓ publish {job_id}
          RabbitMQ (sr_jobs)
            ↓ consume
          flashsr worker (GPU) → super_resolve.enhance_file()
            ↓
          output/*.wav
```

Single-user. Resume по файлам в `output/` + журнал в SQLite.

---

## Этап 1 — MVP (текущая реализация)

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

### Worker
- [x] `scripts/worker.py` — consumer RabbitMQ, prefetch=1
- [x] модель грузится один раз при старте
- [x] skip если `output` уже есть
- [x] cleanup `_48k.wav` при незавершённой обработке
- [x] обновление статусов в SQLite

### Web UI
- [x] загрузка файлов (multipart) → `input/`
- [x] постановка в очередь (job + RabbitMQ)
- [x] кнопка «Обработать всё из input»
- [x] таблица истории/очереди (HTMX poll каждые 3 с)
- [x] скачивание готовых WAV
- [x] опциональный пароль `APP_PASSWORD` (HTTP Basic)

### Mock-режим (локальный тест UI)
- [x] `MOCK_MODE=1` в `.env`
- [x] `worker-mock` контейнер (без GPU, без FlashSR)
- [x] имитация: пауза + passthrough WAV 44.1 kHz
- [x] баннер «MOCK MODE» в Web UI

- [x] `scripts/super_resolve.py` — ручной запуск без GUI

### Документация
- [x] README — раздел Web UI
- [x] `.env.example` — RabbitMQ, APP_PASSWORD, DATABASE_PATH

---

## Этап 2 — Live progress (не реализован)

- [ ] WebSocket или SSE endpoint
- [ ] worker пишет прогресс чанков (опционально в SQLite или pub/sub)
- [ ] полоска прогресса на активной задаче

---

## Этап 3 — Улучшения (не реализован)

- [ ] Celery вместо raw consumer (если устанет поддерживать worker)
- [ ] soxr в ffmpeg, clip перед записью
- [ ] удаление задач / очистка истории из UI
- [ ] lowpass toggle в UI

---

## Этап 4 — Мультиюзер (не планируется, 99.99% один пользователь)

- [ ] таблица `users`, auth, изоляция каталогов
- [ ] только если появится реальная потребность

---

## Запуск этапа 1

```bash
cp .env.example .env
make build
make up
# http://localhost:8080
```

RabbitMQ Management: http://localhost:15672 (guest/guest)
