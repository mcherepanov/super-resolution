.PHONY: build up down stop start status console enhance logs clone

-include .env
export

WEB_PORT ?= 8080
HF_REPO  ?= laion/FlashSR_One-step_Versatile_Audio_Super-resolution
WEIGHTS_DST ?= ./volumes/FlashSR/weights

build:
ifeq ($(MOCK_MODE),1)
	docker compose --profile mock build
else
	docker compose --profile gpu build
endif

up:
ifeq ($(MOCK_MODE),1)
	docker compose --profile mock up -d
else
	docker compose --profile gpu up -d
endif

start: up

down:
	docker compose --profile gpu --profile mock down

stop:
	docker compose --profile gpu --profile mock stop

status:
	@echo "── compose ──────────────────────────────────"
	@docker compose ps -a 2>/dev/null || docker-compose ps -a
	@echo ""
	@echo "── режим ────────────────────────────────────"
	@echo "  MOCK_MODE=$(MOCK_MODE)"
	@echo "  Web UI:    http://localhost:$(WEB_PORT)"
	@echo "  RabbitMQ:  http://localhost:15672  (guest/guest)"
	@echo ""
	@echo "── веса ($(WEIGHTS_DST)) ─────────────────────"
	@if [ -f "$(WEIGHTS_DST)/student_ldm.pth" ]; then \
		ls -lh "$(WEIGHTS_DST)"/*.pth 2>/dev/null; \
	else \
		echo "  не найдены — make clone"; \
	fi

logs:
ifeq ($(MOCK_MODE),1)
	docker compose logs -f web worker-mock rabbitmq
else
	docker compose logs -f web flashsr rabbitmq
endif

console:
	docker exec -it flashsr_gpu /bin/bash

enhance:
	docker exec flashsr_gpu python3 scripts/super_resolve.py \
		-i /app/input -o /app/output -w /app/weights

# Скачать веса с HuggingFace (HUGGINGFACE_TOKEN в .env)
clone:
ifeq ($(MOCK_MODE),1)
	@echo "clone недоступен: MOCK_MODE=1 (веса FlashSR не нужны для теста UI)"
else
	@mkdir -p "$(WEIGHTS_DST)" .hf_staging
	@echo "HF repo: $(HF_REPO)"
	@if [ -z "$(HUGGINGFACE_TOKEN)" ]; then \
		echo "HUGGINGFACE_TOKEN не задан — публичное скачивание"; \
	fi
	@if command -v huggingface-cli >/dev/null 2>&1; then \
		if [ -n "$(HUGGINGFACE_TOKEN)" ]; then \
			huggingface-cli download $(HF_REPO) --include "weights/*.pth" \
				--local-dir .hf_staging --token "$(HUGGINGFACE_TOKEN)"; \
		else \
			huggingface-cli download $(HF_REPO) --include "weights/*.pth" \
				--local-dir .hf_staging; \
		fi; \
	elif python3 -m huggingface_hub.cli.huggingface_cli --help >/dev/null 2>&1; then \
		if [ -n "$(HUGGINGFACE_TOKEN)" ]; then \
			python3 -m huggingface_hub.cli.huggingface_cli download $(HF_REPO) \
				--include "weights/*.pth" --local-dir .hf_staging \
				--token "$(HUGGINGFACE_TOKEN)"; \
		else \
			python3 -m huggingface_hub.cli.huggingface_cli download $(HF_REPO) \
				--include "weights/*.pth" --local-dir .hf_staging; \
		fi; \
	else \
		echo "Нужен huggingface-hub: pip install huggingface-hub"; exit 1; \
	fi
	@cp -n .hf_staging/weights/*.pth "$(WEIGHTS_DST)/" 2>/dev/null || \
		cp .hf_staging/weights/*.pth "$(WEIGHTS_DST)/"
	@rm -rf .hf_staging
	@echo "Готово: $(WEIGHTS_DST)"
endif
