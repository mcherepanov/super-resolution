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
- [x] Material Design 3 стили

### Mock-режим (локальный тест UI)
- [x] `MOCK_MODE=1` в `.env`
- [x] `worker-mock` контейнер (без GPU, без FlashSR)
- [x] имитация: пауза + passthrough WAV 44.1 kHz
- [x] баннер «MOCK MODE» в Web UI
- [x] `make clone` заблокирован при MOCK_MODE

### CLI
- [x] `scripts/super_resolve.py` — ручной запуск без GUI

### Makefile
- [x] `start` / `stop` / `status` / `logs`
- [x] `clone` — веса с HuggingFace

### Git и секреты
- [x] репозиторий на [GitVerse](https://gitverse.ru/Max_Cherep/super-resolution)
- [x] `.gitignore` (`.env`, веса ~3 ГБ, аудио, `data/app.db`)
- [x] SSH-ключ для `git@gitverse.ru`
- [x] `make encode` / `make decode` — ansible-vault (`.env` ↔ `.env.vault`)
- [x] `.env.vault` в git, `.vault_pass` локально

### Документация
- [x] README — Web UI, MOCK_MODE, vault, clone
- [x] `.env.example`

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

## Этап 5 — GitVerse CI/CD (не реализован)

Платформа: [gitverse.ru](https://gitverse.ru) — российский аналог GitHub (СберТех), инфраструктура в РФ.

### Что даёт GitVerse (кратко)
- CI/CD: workflow YAML в `.gitverse/workflows/` (совместим с `.github/workflows/`)
- облачные раннеры + **self-hosted** (нужен для GPU)
- реестр пакетов: Docker, npm, Maven и др.
- PR, issues, wiki, API
- GigaCode (AI-ревью), GigaIDE Cloud, Pages

### План интеграции
- [ ] `.gitverse/workflows/deploy.yml` — деплой по push в `master`
- [ ] self-hosted runner на GPU-сервере ([документация](https://gitverse.ru/docs/cicd/docs/runners/self-hosted))
- [ ] шаги pipeline: `git pull` → `make decode` → `make clone` (если нет весов) → `make build` → `make up`
- [ ] secrets в GitVerse CI для vault-пароля (или `.vault_pass` на сервере)
- [ ] (опционально) push Docker-образов в registry GitVerse
- [ ] (опционально) workflow для lint/smoke-теста в MOCK_MODE на облачном раннере

### Почему self-hosted
GPU-обработка FlashSR (~6 ГБ VRAM) — облачный раннер GitVerse GPU не подходит; runner на своей машине.

---

## Запуск

```bash
git pull
make decode          # .env из .env.vault
make clone             # веса (MOCK_MODE=0)
make build && make up
# http://localhost:8080
```

RabbitMQ Management: http://localhost:15672 (guest/guest)
