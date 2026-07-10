.PHONY: build up down stop start status console enhance logs clone encode decode admin reset

-include .env
export

WEB_PORT ?= 8080
ADMINER_PORT ?= 8081
HF_REPO  ?= laion/FlashSR_One-step_Versatile_Audio_Super-resolution
WEIGHTS_DST ?= ./volumes/FlashSR/weights
ENV_FILE ?= .env
ENV_VAULT ?= .env.vault
VAULT_PASS_FILE ?= .vault_pass
INPUT_DIR ?= ./input
OUTPUT_DIR ?= ./output
DATA_DIR ?= ./data
DB_FILE ?= $(DATA_DIR)/app.db
QUEUE_NAME ?= sr_jobs
RABBIT_CONTAINER ?= sr_rabbitmq

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
	docker compose --profile gpu --profile mock --profile admin down

stop:
	docker compose --profile gpu --profile mock stop

admin:
	docker compose --profile admin up -d db-admin
	@echo "БД UI: http://localhost:$(ADMINER_PORT)"
	@echo "  SQLite: data/app.db (без логина)"

status:
	@echo "── compose ──────────────────────────────────"
	@docker compose ps -a 2>/dev/null || docker-compose ps -a
	@echo ""
	@echo "── режим ────────────────────────────────────"
	@echo "  MOCK_MODE=$(MOCK_MODE)"
	@if [ "$(MOCK_MODE)" = "1" ]; then echo "  AI в UI:   нет (mock)"; else echo "  AI в UI:   да (gpu)"; fi
	@echo "  Web UI:    http://localhost:$(WEB_PORT)"
	@lan_ip=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	if [ -n "$$lan_ip" ]; then echo "  LAN:       http://$$lan_ip:$(WEB_PORT)"; fi
	@echo "  RabbitMQ:  http://localhost:15672  (guest/guest)"
	@if docker ps --format '{{.Names}}' 2>/dev/null | grep -q sr_db_admin; then \
		echo "  БД UI:     http://localhost:$(ADMINER_PORT)  (sqlite-web)"; \
	else \
		echo "  БД UI:     make admin  (http://localhost:$(ADMINER_PORT))"; \
	fi
	@echo ""
	@echo "── веса ($(WEIGHTS_DST)) ─────────────────────"
	@if [ -f "$(WEIGHTS_DST)/student_ldm.pth" ]; then \
		ls -lh "$(WEIGHTS_DST)"/*.pth 2>/dev/null; \
	else \
		echo "  не найдены — make clone"; \
	fi
	@echo ""
	@echo "── ffmpeg (mock worker) ─────────────────────"
	@docker exec sr_worker_mock ffmpeg -version 2>/dev/null | head -1 \
		|| echo "  worker-mock не запущен"

logs:
ifeq ($(MOCK_MODE),1)
	docker compose logs -f web worker-mock rabbitmq
else
	docker compose logs -f web flashsr rabbitmq
endif

console:
	docker exec -it sr_flashsr /bin/bash

enhance:
	docker exec sr_flashsr python3 scripts/super_resolve.py \
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

# Секреты: .env ↔ .env.vault (ansible-vault). Требует: apt install ansible-core
encode:
	@command -v ansible-vault >/dev/null 2>&1 || { \
		echo "Нужен ansible-vault: sudo apt install ansible-core"; exit 1; }
	@test -f "$(ENV_FILE)" || { echo "Нет $(ENV_FILE)"; exit 1; }
	@if [ -f "$(VAULT_PASS_FILE)" ]; then \
		ansible-vault encrypt "$(ENV_FILE)" --output "$(ENV_VAULT)" \
			--vault-password-file "$(VAULT_PASS_FILE)"; \
	else \
		ansible-vault encrypt "$(ENV_FILE)" --output "$(ENV_VAULT)"; \
	fi
	@chmod 600 "$(ENV_VAULT)"
	@echo "Готово: $(ENV_VAULT) (можно коммитить в git)"

decode:
	@command -v ansible-vault >/dev/null 2>&1 || { \
		echo "Нужен ansible-vault: sudo apt install ansible-core"; exit 1; }
	@test -f "$(ENV_VAULT)" || { echo "Нет $(ENV_VAULT) — git pull или make encode"; exit 1; }
	@if [ -f "$(VAULT_PASS_FILE)" ]; then \
		ansible-vault decrypt "$(ENV_VAULT)" --output "$(ENV_FILE)" \
			--vault-password-file "$(VAULT_PASS_FILE)"; \
	else \
		ansible-vault decrypt "$(ENV_VAULT)" --output "$(ENV_FILE)"; \
	fi
	@chmod 600 "$(ENV_FILE)"
	@echo "Готово: $(ENV_FILE)"

# Полный сброс: input/output, таблица jobs (+ autoincrement), очередь RabbitMQ
reset:
	@echo "=== reset: $(INPUT_DIR), $(OUTPUT_DIR), jobs, $(QUEUE_NAME) ==="
	@docker compose --profile gpu --profile mock stop flashsr worker-mock 2>/dev/null || true
	@if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '$(RABBIT_CONTAINER)'; then \
		docker exec $(RABBIT_CONTAINER) rabbitmqctl purge_queue '$(QUEUE_NAME)' \
			&& echo "RabbitMQ: очередь $(QUEUE_NAME) очищена" \
			|| echo "RabbitMQ: очередь $(QUEUE_NAME) не найдена или пуста"; \
	else \
		echo "RabbitMQ: контейнер $(RABBIT_CONTAINER) не запущен — очередь не очищена"; \
	fi
	@mkdir -p "$(INPUT_DIR)" "$(OUTPUT_DIR)"
	@find "$(INPUT_DIR)" -mindepth 1 -delete 2>/dev/null || true
	@find "$(OUTPUT_DIR)" -mindepth 1 -delete 2>/dev/null || true
	@echo "Каталоги: $(INPUT_DIR), $(OUTPUT_DIR) — очищены"
	@if [ -f "$(DB_FILE)" ]; then \
		_py='import sqlite3; c=sqlite3.connect(_DB); c.execute("DELETE FROM jobs"); c.execute("DELETE FROM sqlite_sequence WHERE name='"'"'jobs'"'"'"); c.commit(); c.close()'; \
		if python3 -c "_DB='$(DB_FILE)'; $$_py" 2>/dev/null; then \
			echo "SQLite: jobs очищена, id сброшен"; \
		elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'sr_web'; then \
			docker exec sr_web python3 -c "_DB='/app/data/app.db'; $$_py" \
			&& echo "SQLite: jobs очищена, id сброшен (через sr_web)"; \
		else \
			echo "SQLite: нет прав на $(DB_FILE) — запустите make up или: sudo chown $$USER $(DB_FILE)"; \
			exit 1; \
		fi; \
	else \
		echo "SQLite: $(DB_FILE) не найден — пропуск"; \
	fi
ifeq ($(MOCK_MODE),1)
	@docker compose --profile mock start worker-mock 2>/dev/null \
		&& echo "worker-mock: запущен" || echo "worker-mock: не запущен (make up)"
else
	@docker compose --profile gpu start flashsr 2>/dev/null \
		&& echo "flashsr: запущен" || echo "flashsr: не запущен (make up)"
endif
	@echo "Готово."
