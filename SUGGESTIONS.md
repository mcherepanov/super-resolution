# Анализ проекта super-resolution и предложения

Дата: 2026-07-08  
Основной скрипт: `vendor/FlashSR/scripts/super_resolve.py`

---

## 1. Обзор проекта

| Компонент | Назначение |
|-----------|------------|
| `vendor/FlashSR/` | Код модели FlashSR (KAIST) + скрипты инференса |
| `volumes/FlashSR/weights/` | Веса: `student_ldm.pth`, `sr_vocoder.pth`, `vae.pth` (~3.1 ГБ) |
| `docker/Dockerfile` | CUDA 12.2, PyTorch cu118, ffmpeg |
| `compose.yml` | GPU-контейнер `flashsr_gpu` |
| `Makefile` | build / up / down / console |

Проект — Docker-обёртка вокруг [FlashSR](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution), адаптированная под пакетную обработку музыки с выходом 44.1 kHz.

---

## 2. Анализ `scripts/super_resolve.py`

### 2.1. Что сделано хорошо

1. **Корректная частота для модели** — обработка на 48 kHz (`WINDOW_LEN = 245_760`), как требует FlashSR.
2. **Overlap-add** — длинные треки режутся на окна 5.12 с с перекрытием 0.5 с и кроссфейдом.
3. **Пакетная обработка** — рекурсивный обход каталога, сохранение структуры папок.
4. **Resume** — пропуск уже готовых файлов, очистка «битого» состояния (`*_48.wav` без финального `.wav`).
5. **Стерео** — каналы L/R обрабатываются отдельно (модель моно).
6. **UX** — прогресс-бар, ETA, таймер, отчёт по sample rate и скорости.
7. **Выход 44.1 kHz** — через ffmpeg после нативной обработки на 48 kHz.

### 2.2. Архитектура пайплайна

```
вход (любой SR) → scipy resample_poly → 48 kHz
    → FlashSR (окна 245760, overlap-add)
    → временный WAV 48 kHz
    → ffmpeg -ar 44100 → финальный WAV
```

Модель (`FlashSR.forward`):
- опционально lowpass (Chebyshev, sr=48000 захардкожен внутри модели);
- one-step diffusion (DPM-Solver);
- VAE + SR-vocoder на выходе.

### 2.3. Замеченные проблемы

#### Критичные

| # | Проблема | Где | Рекомендация |
|---|----------|-----|--------------|
| 1 | **Утечка файловых дескрипторов** | строки 98–99, 119–120 | `open(os.devnull)` в цикле без закрытия. Заменить на `with open(os.devnull, "w") as devnull: redirect_stdout(devnull)`. |
| 2 | **Нет `.gitignore`** | корень проекта | `.env` с токеном HuggingFace попадёт в git. Добавить `.gitignore` с `.env`, `__pycache__/`, `*_48.wav`. |

#### Средние

| # | Проблема | Где | Рекомендация |
|---|----------|-----|--------------|
| 3 | **Хрупкий путь temp-файла** | `str(dst).replace(".wav", "_48.wav")` | Использовать `dst.with_name(dst.stem + "_48.wav")` или `tempfile`. `replace` сломается на путях вроде `my.wav.backup/`. |
| 4 | **Два разных ресемплера** | scipy (вход) + ffmpeg (выход) | Для консистентности — один движок. Варианты: `resample_poly` и на выходе; или ffmpeg/soxr на обоих этапах. Для музыки soxr часто предпочтительнее. |
| 5 | **ffmpeg без параметров качества** | `ffmpeg -i ... -ar 44100` | Добавить `-af aresample=resampler=soxr` или `-sample_fmt s32` / `-c:a pcm_s24le` для контроля качества. |
| 6 | **Скрипт вне vendor/** | `scripts/super_resolve.py` | Уже вынесен; `vendor/` — только upstream. |
| 7 | **Docker не устанавливает пакет** | `Dockerfile` | Нет `pip install -e vendor/FlashSR`. При запуске нужен ручной `PYTHONPATH` или установка. |
| 8 | **Жёсткий путь в compose** | `/home/user/teque_input/music` | Вынести в `.env`: `INPUT_DIR`, `OUTPUT_DIR`. |
| 9 | **Папка `src/` смонтирована, но не существует** | `compose.yml` | Создать или убрать volume. |

#### Низкий приоритет

| # | Проблема | Рекомендация |
|---|----------|--------------|
| 10 | Стерео последовательно (2× время) | Батч `(2, T)` если модель поддерживает batch>1, либо `torch.cuda.Stream` для параллелизма. |
| 11 | `warnings.filterwarnings("ignore")` | Сузить до конкретных предупреждений (torch, librosa). |
| 12 | Нет нормализации/клиппинга перед записью | `np.clip(result, -1.0, 1.0)` или peak-normalize с запасом headroom (-1 dBFS). |
| 13 | `TQDM_DISABLE=1` глобально | Достаточно не импортировать tqdm; переменная избыточна. |
| 14 | MP3/OGG через soundfile | Зависит от сборки libsndfile. Проверить в Docker или fallback через ffmpeg→wav. |

---

## 3. Качество звука (музыка)

### 3.1. Флаг `--lowpass`

По умолчанию `lowpass=False` — **правильно для полнополосной музыки** (CD, FLAC, хорошие стримы).

Включать `--lowpass`, если:
- источник уже ограничен по полосе (телефония, AM, сильно сжатый поток);
- нужно «достроить» ВЧ сверх реальной полосы входа.

При `lowpass=True` модель делает CPU roundtrip (tensor→numpy→filter→tensor) на каждый чанк — заметно медленнее.

### 3.2. Ресемплинг 48 → 44.1 kHz

Модель выдаёт 48 kHz. Для CD/плееров финальный 44.1 kHz — разумно.

Риск: двойной ресемплинг (вход ≠ 48 kHz → scipy; выход → ffmpeg) накапливает артефакты. Если вход уже 44.1 kHz:
- сейчас: 44.1 → 48 (scipy) → enhance → 44.1 (ffmpeg);
- лучше: минимизировать число конверсий или использовать soxr везде.

### 3.3. Overlap-add

Текущая схема: fade-in на новых чанках + нормализация по сумме весов. Работоспособна, но на стыках возможны микро-артефакты на перкуссии/атаках.

Варианты улучшения:
- симметричный кроссфейд (fade-out на хвосте предыдущего + fade-in);
- увеличить `OVERLAP` до 48_000 (1 с) для музыки с длинными реверб-хвостами;
- вынести `WINDOW_LEN` / `OVERLAP` в CLI-параметры.

### 3.4. Постобработка (опционально)

Модель может добавлять «синтетические» ВЧ. Для музыки иногда помогает:
- мягкий shelf-EQ выше 16–18 kHz;
- лимитер на выходе;
- A/B с оригиналом (blind test).

Это уже вкусовщина — в скрипт не обязательно, но полезно для экспериментов.

---

## 4. Инфраструктура (Docker)

### Текущие проблемы

```dockerfile
# Dockerfile ставит torch, но НЕ:
# RUN pip install -e /app/vendor/FlashSR
```

```yaml
# compose.yml
volumes:
  - ./src:/app/src          # папки нет
  - /home/user/teque_input/music:/app/input  # чужой путь
```

### Предложения

1. **Dockerfile** — добавить:
   ```dockerfile
   COPY vendor/FlashSR /app/vendor/FlashSR
   RUN pip install -e /app/vendor/FlashSR
   ENV PYTHONPATH=/app/vendor/FlashSR
   ```

2. **compose.yml** — параметризовать через `.env`:
   ```yaml
   volumes:
     - ${INPUT_DIR:-./input}:/app/input
     - ${OUTPUT_DIR:-./output}:/app/output
     - ./volumes/FlashSR/weights:/app/weights
   ```

3. **Makefile** — цели для типичного запуска:
   ```makefile
   enhance:
       docker exec flashsr_gpu python vendor/FlashSR/scripts/super_resolve.py \
         -i /app/input -o /app/output -w /app/weights
   ```

4. **Версии CUDA** — образ `cuda:12.2`, PyTorch `cu118`. Работает через совместимость, но лучше выровнять (cu121/cu122) при следующей пересборке.

---

## 5. Производительность

| Фактор | Оценка |
|--------|--------|
| Загрузка модели | ~3 ГБ, один раз при старте — ок |
| VRAM | ~6 ГБ на чанк (по README) |
| Стерео | 2 последовательных прохода |
| Lowpass | CPU на каждый forward — избегать без нужды |
| redirect_stdout | лишние open() в цикле — мелкий, но поправимый overhead |

Идеи ускорения:
- `torch.compile(model)` (PyTorch 2.x) — проверить на вашей GPU;
- mixed precision `autocast` — осторожно, может ухудшить качество;
- кэширование fade-весов на GPU (`fade.to(device)`).

---

## 6. Приоритетный план действий

### Быстрые правки (1–2 часа)

1. Исправить утечку `open(os.devnull)` в `scripts/super_resolve.py`.
2. Заменить `str.replace` на `Path` для temp-файла.
3. Добавить `.gitignore` (`.env`, `__pycache__`, `*_48.wav`, `output/`).
4. Добавить `np.clip` перед `sf.write`.

### Средний срок

6. Объединить скрипты enhance в один модуль с CLI-флагами.
7. Параметризовать пути в `compose.yml`.
8. Установить пакет FlashSR в Dockerfile.
9. Улучшить ffmpeg-ресемплинг (soxr).

### Долгий срок

10. Симметричный overlap-add + настраиваемые окна.
11. Постобработка (лимитер, опциональный EQ).
12. Бенчмарк/лог метрик (RTF, peak, crest factor) в JSON для сравнения настроек.

---

## 7. Пример исправления утечки дескрипторов

```python
@contextlib.contextmanager
def _suppress_stdout():
    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull):
            yield

# использование:
with _suppress_stdout():
    out = model(chunk, lowpass_input=lowpass)
```

---

## 8. Сравнение скриптов

**Рекомендация:** `scripts/super_resolve.py` — единственный рабочий скрипт проекта.

---

## 9. Ссылки

- [FlashSR на HuggingFace](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution)
- [Оригинальный AudioSR](https://github.com/haoheliu/versatile_audio_super_resolution)
- [Статья FlashSR (arXiv:2501.10807)](https://arxiv.org/abs/2501.10807)
