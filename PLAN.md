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
- [x] **«Скачать все готовые (ZIP)»** — `POST /download/ready`, общая галочка «удалить из output»

### Админка БД
- [x] `db-admin` (coleifer/sqlite-web) — профиль `admin`, `make admin`
- [x] http://localhost:8081 — без логина, `data/app.db`

---

## Этап 2 — Прогресс обработки (в работе)

### Проблема сейчас

1. **Консоль (docker logs):** FlashSR печатает свой tqdm `sample time step: 1/1` на **stderr** — это шаг диффузии внутри чанка (one-step модель), не номер чанка. Наш бар `Progress: [████] 15/35` идёт на **stdout** с `\r` — в не-TTY логах теряется или смешивается с tqdm.
2. **Web UI:** HTMX poll 3 с обновляет только `status` (`queued` / `processing` / `done`), полей прогресса в БД нет.

Цель: **один понятный прогресс** в консоли и **полоска + подпись** в таблице задач (poll, без WebSocket на первом шаге).

---

### Архитектура

```
enhance() / run_pipeline() / cue_batch
        ↓ callback (throttled)
  scripts/progress.py  →  ProgressReporter
        ├─ console backend   (один бар, TTY / non-TTY)
        └─ db backend        (update_job, ≤1 раз/с)
                ↓
          SQLite jobs.progress_pct, jobs.progress_detail
                ↓
          HTMX GET /jobs/partial (every 3s, при желании 1s если есть processing)
                ↓
          jobs_table.html — <progress> + текст
```

**Почему SQLite, не SSE:** один пользователь, poll уже есть, общий volume `data/` — достаточно для MVP. SSE/WebSocket — опционально позже, если 3 с покажется грубо.

---

### 2.1 — Консоль: один бар «как раньше»

- [x] **Заглушить vendor tqdm** — `suppress_model_io()` (stdout + stderr) в `super_resolve.py`
- [x] **`scripts/progress.py`** — `ProgressReporter` (TTY / non-TTY)
- [x] Логика бара в reporter; `enhance(..., on_progress=...)`
- [x] **Стерeo:** `AI · L` / `AI · R`, суммарный % через `JobProgress`
- [x] `TQDM_DISABLE` в `worker.py` до импортов

### 2.2 — БД и worker

- [x] Миграция: `progress_pct`, `progress_detail`
- [x] `update_job_progress()` — throttle 1 с
- [x] Очистка progress при `done` / `failed`
- [x] `audio_pipeline.run_pipeline(..., progress=...)`
- [x] `worker.py` — `ProgressReporter` + `JobProgress`
- [x] **`cue_batch`:** «Трек k/m» через `set_batch()`

### 2.3 — Web UI (polling)

- [x] Полоска + подпись в `jobs_table.html` для `processing`
- [x] CSS `.job-progress`
- [ ] Poll 1 с при active processing (опционально)

### 2.4 — Граничные случаи

- [x] Job без AI — прогресс по этапам ffmpeg
- [x] MOCK — fake progress в `mock_enhance_sleep`
- [x] Короткий файл — `1/1` чанков (не diffusion step)
- [x] CLI без job_id — только console backend
- [ ] SSE/WebSocket (не в первой итерации)

---

### 2.1 (архив описания) — Консоль: один бар «как раньше»

- [x] **Заглушить vendor tqdm** при вызове `model(...)`: расширить `_suppress_stdout()` → `_suppress_model_io()` (redirect **stdout + stderr** на devnull на время inference). `TQDM_DISABLE=1` оставить в `worker.py` / `super_resolve.py` до импорта FlashSR — belt and suspenders.
- [ ] **`scripts/progress.py`** — `ProgressReporter`:
  - `report(current, total, *, label, phase, eta_sec=None)`
  - **TTY** (`sys.stderr.isatty()`): одна строка `\r`, как сейчас в `super_resolve.enhance`
  - **non-TTY** (docker logs): печать **новой строки** только при изменении `current` или раз в N сек (не спамить одинаковым `15/35`)
  - префикс: `Job {id} | {phase} |` — чтобы в логах worker было видно, какой job
- [ ] Перенести логику бара из `super_resolve.enhance()` в reporter; `enhance(..., on_progress=None)` вызывает callback после каждого чанка
- [ ] **Стерeo:** суммарный счётчик `current/total` = `chunks_L + chunks_R`, подпись `AI · L` / `AI · R` (или `канал L: 15/35`, потом `канал R: 8/35`)
- [ ] Финальные строки без `\r`: `Done!`, `Resampling... Done` — как сейчас

**Не показывать** `sample time step` пользователю — это внутренность FlashSR, не несёт смысла при one-step.

---

### 2.2 — БД и worker

- [ ] Миграция в `db.py`:
  - `progress_pct REAL` — 0…100, `NULL` для queued/done/failed
  - `progress_detail TEXT` — человекочитаемо: `Декодирование`, `Фильтр: compand`, `AI · L · 15/35 · ETA 42s`, `Экспорт flac`
- [ ] `update_job_progress(job_id, pct, detail)` — обёртка с **throttle 1 с** (monotonic), чтобы не дёргать WAL на каждый чанк (~8/s)
- [ ] При `status=done|failed` — обнулить/очистить progress или оставить 100% + «Готово» до следующего poll (на выбор: очистить в `update_job(..., status='done')`)
- [ ] **`audio_pipeline.run_pipeline`:** отчёты по этапам:
  - decode → ~5%
  - каждый включённый фильтр → равные доли до AI
  - enhance → основная доля (если AI выкл — фильтры + export делят 100%)
  - resample/export → хвост
  - веса этапов зафиксировать константами в `progress.py` (не магические числа в pipeline)
- [ ] **`worker.py`:** перед `run_pipeline` создать `ProgressReporter(job_id=...)`, передать в pipeline/enhance
- [ ] **`cue_batch`:** `progress_detail = "Трек 3/12 · …"`, `progress_pct` = `(track_index-1 + sub_pct/100) / total_tracks * 100`

---

### 2.3 — Web UI (polling)

- [ ] В `jobs_table.html` для `status == 'processing'`: колонка или строка под статусом:
  - `<progress value="{{ job.progress_pct }}" max="100">`
  - текст `{{ job.progress_detail }}`
- [ ] CSS: полоска в стиле MD3 (уже есть `--status-processing-*`)
- [ ] Poll: оставить **3 с** по умолчанию; опционально `every 1s` только если в partial есть хотя бы один `processing` (HTMX `hx-trigger` через OOB или отдельный флаг в шаблоне — мелкая доработка)
- [ ] `queued`: «В очереди» без полоски или indeterminate (пульсация CSS)
- [ ] Отдельный JSON endpoint **не обязателен** — partial уже отдаёт HTML; при необходимости позже `GET /api/jobs/{id}`

---

### 2.4 — Граничные случаи

- [ ] Job без AI (только ffmpeg): прогресс по этапам фильтров, без чанков
- [ ] MOCK_MODE: fake progress по `_mock_enhance_delay` (sleep + процент) или хотя бы «MOCK AI…»
- [ ] Короткий файл (≤1 чанк): всё равно показать `1/1` чанков — уже не путать с diffusion step
- [ ] Ошибка на середине: `progress_detail` сохранить + `status=failed`
- [ ] CLI `super_resolve.py` без job_id: только console backend, без записи в SQLite

---

### Порядок реализации

1. `progress.py` + заглушка stderr + рефактор `enhance()` (консоль)
2. Миграция БД + throttle + wiring pipeline/worker
3. UI partial + CSS
4. cue_batch + MOCK
5. (опционально) poll 1 s при active processing; SSE — только если попросят

### Критерий готовности

- В `docker logs -f sr_flashsr` при AI: **одна** обновляемая/построчная строка `Job N | AI · L | [██░░] 43% (15/35) ETA 40s`, **без** `sample time step`
- В UI при `processing`: полоска и подпись обновляются poll'ом без перезагрузки страницы
- cue_batch: видно «Трек k/m»

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
