# super-resolution

Docker-обёртка для улучшения качества аудио (речь, музыка, звуковые эффекты) с помощью модели **FlashSR**.

Проект основан на редистрибуции [laion/FlashSR_One-step_Versatile_Audio_Super-resolution](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution) и переработан под пакетную обработку музыки с выходом **44.1 kHz (CD)**.

## Источники и авторы

Вся заслуга за архитектуру модели, исследование, обучение и веса принадлежит оригинальным авторам. Этот репозиторий с ними **не аффилирован**.

| | |
|---|---|
| **Авторы FlashSR** | Jaekwon Im and Juhan Nam (KAIST) |
| **Статья** | [FlashSR: One-step Versatile Audio Super-resolution via Diffusion Distillation](https://arxiv.org/abs/2501.10807) (arXiv:2501.10807) |
| **Демо** | [jakeoneijk.github.io/flashsr-demo](https://jakeoneijk.github.io/flashsr-demo/) |
| **Оригинальный код FlashSR** | [jakeoneijk/FlashSR_Inference](https://github.com/jakeoneijk/FlashSR_Inference) |
| **Оригинальные веса** | [jakeoneijk/FlashSR_weights](https://huggingface.co/datasets/jakeoneijk/FlashSR_weights) |
| **Редистрибуция (основа vendor/)** | [laion/FlashSR_One-step_Versatile_Audio_Super-resolution](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution) |
| **Предшественник (AudioSR)** | [haoheliu/versatile_audio_super_resolution](https://github.com/haoheliu/versatile_audio_super_resolution) |

> Есть и другие несвязанные проекты с названием «FlashSR».

## Что делает FlashSR

FlashSR восстанавливает высокочастотные компоненты аудио за **один проход** диффузионной модели. Вход любой sample rate → ресемплинг в 48 kHz → реконструкция недостающих ВЧ.

Применение:

- апскейл записей с низкой частотой дискретизации;
- улучшение аудио после lossy-кодеков, вокодеров и т.п.;
- постобработка TTS / voice conversion.

Модель работает с речью, музыкой и звуковыми эффектами.

## Структура проекта

```
super-resolution/
├── web/app/                    # FastAPI + HTMX UI
├── scripts/
│   ├── super_resolve.py        # CLI (ручной запуск)
│   ├── worker.py               # GPU worker (RabbitMQ)
│   └── db.py                   # SQLite журнал задач
├── vendor/FlashSR/             # код модели (upstream)
├── data/app.db                 # история обработок
├── volumes/FlashSR/weights/    # веса (~3.1 ГБ)
├── docker/
├── compose.yml
├── PLAN.md                     # план этапов
├── input/                      # входные файлы
└── output/                     # результаты
```

## Требования

- Docker + NVIDIA Container Toolkit
- GPU с ~6 ГБ VRAM
- Веса в `volumes/FlashSR/weights/` (скачать с [HuggingFace](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution/tree/main/weights) или из [jakeoneijk/FlashSR_weights](https://huggingface.co/datasets/jakeoneijk/FlashSR_weights))

## Быстрый старт (Web UI)

```bash
cp .env.example .env
make build
make up
```

Открыть **http://localhost:8080** — загрузка файлов, очередь, история с датами.

RabbitMQ Management: http://localhost:15672 (guest/guest)

```bash
make status    # контейнеры, URL, веса
make start     # alias для make up
make stop
make clone     # скачать веса с HuggingFace (HUGGINGFACE_TOKEN в .env)
make logs
make down
```

Worker стартует автоматически (`flashsr` контейнер). Модель грузится один раз; задачи берутся из RabbitMQ.

### Resume

- готовые файлы в `output/` → статус `skipped` / не ставятся в очередь;
- прервать `make down` / Ctrl+C — необработанные задачи остаются в очереди RabbitMQ;
- история с датами — в SQLite (`data/app.db`) и в таблице на сайте.

## CLI (без Web UI)

```bash
make enhance
```

Или вручную:

```bash
make console
python3 scripts/super_resolve.py -i /app/input -o /app/output -w /app/weights
```

## Использование скрипта

```bash
# один файл
python3 scripts/super_resolve.py -i track.flac -o track_enhanced.wav

# весь каталог (рекурсивно, структура папок сохраняется)
python3 scripts/super_resolve.py -i ./input -o ./output

# lowpass перед enhance (для узкополосного входа)
python3 scripts/super_resolve.py -i ./input -o ./output --lowpass

# выбор GPU
CUDA_VISIBLE_DEVICES=0 python3 scripts/super_resolve.py -i ./input -o ./output
```

### Особенности `super_resolve.py`

| Параметр | Значение |
|----------|----------|
| Обработка модели | 48 kHz, окна по 245 760 сэмплов (5.12 с) |
| Длинные треки | overlap-add, симметричный кроссфейд (overlap 0.5 с) |
| Выход | 44.1 kHz WAV (через ffmpeg) |
| Каналы | стерео — по каналам L/R |
| Пакетный режим | пропуск уже обработанных файлов |

### Флаг `--lowpass`

По умолчанию выключен — подходит для полнополосной музыки (CD, FLAC).

Включать, если вход уже ограничен по полосе (телефония, сильно сжатый поток). Фильтр применяется перед enhance, чтобы лучше соответствовать обучающему распределению модели.

> **Совет:** при конфликте cudnn в conda: `LD_LIBRARY_PATH="" python3 scripts/super_resolve.py ...`

## Переменные окружения

Скопировать `.env.example` → `.env`:

| Переменная | Описание |
|------------|----------|
| `INPUT_DIR` | каталог с исходниками (default: `./input`) |
| `OUTPUT_DIR` | каталог для результатов (default: `./output`) |
| `WEB_PORT` | порт Web UI (default: `8080`) |
| `APP_PASSWORD` | пароль UI (логин `admin`); пусто = без авторизации |
| `HUGGINGFACE_TOKEN` | токен HF для `make clone` |
| `LOWPASS` | `1` / `true` — lowpass в worker |
| `MOCK_MODE` | `1` — UI-тест без GPU (mock worker, см. ниже) |
| `MOCK_DELAY_SEC` | пауза имитации обработки в mock (default: `3`) |

### MOCK_MODE — тест UI без GPU

В `.env` установить `MOCK_MODE=1`, затем:

```bash
make build
make up
```

Поднимутся `rabbitmq` + `web` + `worker-mock` (без CUDA, без весов ~3 ГБ).  
Worker имитирует обработку (пауза + passthrough через ffmpeg), полный цикл очереди и истории работает.

На сервере с GPU: `MOCK_MODE=0` (или убрать) — поднимается `flashsr` с FlashSR.

## Лицензия

Скрипты `scripts/`, Web UI и инфраструктура этого репозитория — по усмотрению владельца.

Код модели (`vendor/FlashSR/FlashSR/`, `TorchJaekwon/`) и веса — от оригинальных авторов; см. их репозитории для условий лицензирования. Редистрибуция LAION распространяет inference-скрипт под [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Citation

```bibtex
@article{im2025flashsr,
  title={FlashSR: One-step Versatile Audio Super-resolution via Diffusion Distillation},
  author={Im, Jaekwon and Nam, Juhan},
  journal={arXiv preprint arXiv:2501.10807},
  year={2025}
}
```

## Ссылки

- [FlashSR paper](https://arxiv.org/abs/2501.10807)
- [LAION redistribution](https://huggingface.co/laion/FlashSR_One-step_Versatile_Audio_Super-resolution)
- [AudioSR](https://github.com/haoheliu/versatile_audio_super_resolution)
- [NVSR](https://github.com/haoheliu/ssr_eval)
- [BigVGAN](https://github.com/NVIDIA/BigVGAN)
- [Diffusers](https://github.com/huggingface/diffusers)
