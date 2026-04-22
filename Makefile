.PHONY: setup deploy destroy pause resume sync order types test typecheck lint e2e e2e-up e2e-run e2e-down local-up local-down logs stats gateway ssh help

PROJECT = ibkr-bridge
PYTHON ?= .venv/bin/python3
E2E_ENV = .env.test
E2E_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.test.yml -p $(PROJECT)-test --env-file $(E2E_ENV)
E2E_COMPOSE_DOWN = docker compose -f docker-compose.yml -f docker-compose.test.yml -p $(PROJECT)-test --env-file $(E2E_ENV)
LOCAL_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.local.yml
CLI_BRIDGE_ENV = $(if $(ENV),BRIDGE_ENV=$(ENV))

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  make %-12s %s\n", $$1, $$2}'

setup: ## Create .venv and install all dependencies
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt -r services/bridge/requirements.txt
	@echo "$(CURDIR)/services/bridge" > $$(find .venv/lib -name site-packages -type d)/$(PROJECT).pth
	@for f in env env.droplet; do \
		[ -f ".$$f" ] || { cp env_examples/$$f .$$f && echo "  Created .$$f from env_examples/$$f — fill in your values"; }; \
	done
	@[ -f .env.test ] || echo "  NOTE: .env.test not auto-created — cp env_examples/env.test .env.test and set real paper-account TWS_* values before running E2E tests."

deploy: ## Deploy infrastructure (Terraform + Docker)
	$(PYTHON) -m cli deploy

destroy: ## Permanently destroy all infrastructure
	$(PYTHON) -m cli destroy

pause: ## Snapshot droplet + delete (save costs)
	$(PYTHON) -m cli pause

resume: ## Restore droplet from snapshot
	$(PYTHON) -m cli resume

sync: ## Push .env + restart (S=service B=1 LOCAL_FILES=1 ENV=local)
	@. ./.env 2>/dev/null; . ./.env.droplet 2>/dev/null; \
	env="$${BRIDGE_ENV:-$${DEFAULT_CLI_ENV:-prod}}"; \
	[ -n "$(ENV)" ] && env="$(ENV)"; \
	if [ "$$env" = "local" ]; then \
		$(LOCAL_COMPOSE) restart; \
	else \
		$(PYTHON) -m cli sync $(S) $(if $(LOCAL_FILES),--local-files) $(if $(B),--build) $(if $(SKIP_E2E),--skip-e2e); \
	fi

order: ## Place a stock order (e.g. make order Q=2 SYM=TSLA T=MKT [P=] [CUR=EUR] [EX=LSE] [TIF=GTC] [RTH=1] [ENV=local])
	@. ./.env 2>/dev/null; . ./.env.droplet 2>/dev/null; \
	env="$${BRIDGE_ENV:-$${DEFAULT_CLI_ENV:-prod}}"; \
	[ -n "$(ENV)" ] && env="$(ENV)"; \
	BRIDGE_ENV=$$env $(PYTHON) -m cli order $(Q) $(SYM) $(T) $(P) $(if $(CUR),--currency $(CUR)) $(if $(EX),--exchange $(EX)) $(if $(TIF),--tif $(TIF)) $(if $(RTH),--outside-rth)

types: ## Regenerate TypeScript + Python types from Pydantic models
	PYTHONPATH=services/bridge $(PYTHON) schema_gen.py bridge_models > types/typescript/http/types.schema.json
	npx --yes json-schema-to-typescript types/typescript/http/types.schema.json > types/typescript/http/types.d.ts
	@echo "Generated types/typescript/http/types.d.ts"
	$(PYTHON) gen_python_types.py

test: ## Run unit tests
	PYTHONPATH=.:services/bridge $(PYTHON) -m pytest -v

typecheck: ## Run mypy strict type checking
	MYPYPATH=services/bridge $(PYTHON) -m mypy services/bridge/
	$(PYTHON) -m mypy services/shared/
	$(PYTHON) -m mypy types/python/ibkr_bridge_types/

lint: ## Run ruff linter (use FIX=1 to auto-fix)
	$(PYTHON) -m ruff check services/bridge/ services/shared/ cli/ schema_gen.py gen_python_types.py types/python/ibkr_bridge_types/ $(if $(FIX),--fix)
	@if grep -rn '__all__' services/ types/ cli/ --include='*.py'; then echo "ERROR: __all__ is banned — use explicit re-exports"; exit 1; fi

e2e-up: ## Start E2E test stack (ib-gateway + bridge, paper account)
	@test -f $(E2E_ENV) || { echo "ERROR: $(E2E_ENV) not found — copy env_examples/env.test to .env.test and fill in credentials"; exit 1; }
	@if curl -sf http://localhost:15010/health | grep -q '"connected": true'; then \
		echo "Stack already running and connected"; \
	else \
		$(E2E_COMPOSE) up -d --build; \
		echo "Waiting for IB Gateway connection (up to 240s)..."; \
		connected=false; \
		for i in $$(seq 1 80); do \
			if $(E2E_COMPOSE) logs ib-gateway 2>&1 | grep -q "Existing session detected"; then \
				echo "ERROR: Session conflict — another session is using these credentials."; \
				echo "       Close TWS/Gateway elsewhere, then retry."; \
				exit 1; \
			fi; \
			gw_status=$$($(E2E_COMPOSE) ps ib-gateway --format '{{.State}}' 2>/dev/null); \
			if [ "$$gw_status" = "exited" ]; then \
				echo "ERROR: ib-gateway exited unexpectedly. Check logs:"; \
				echo "       $(E2E_COMPOSE) logs ib-gateway"; \
				exit 1; \
			fi; \
			if curl -sf http://localhost:15010/health | grep -q '"connected": true'; then \
				connected=true; \
				echo "Bridge connected to IB Gateway"; break; \
			fi; \
			sleep 3; \
		done; \
		if [ "$$connected" != "true" ]; then \
			echo "ERROR: bridge did not connect to IB Gateway within 240s"; \
			echo "       Check logs: $(E2E_COMPOSE) logs"; \
			exit 1; \
		fi; \
	fi

e2e-down: ## Stop and remove E2E test stack
	$(E2E_COMPOSE_DOWN) down

e2e-run: ## Run E2E tests (stack must be up)
	@$(E2E_COMPOSE) restart bridge > /dev/null 2>&1 && sleep 3
	$(PYTHON) -m pytest services/bridge/tests/e2e/ -v

e2e: ## Run E2E tests (starts/stops stack automatically)
	@test -f $(E2E_ENV) || { echo "ERROR: $(E2E_ENV) not found — copy env_examples/env.test to .env.test and fill in credentials"; exit 1; }
	@was_up=false; \
	if curl -sf http://localhost:15010/health | grep -q '"connected": true'; then \
		was_up=true; \
	fi; \
	$(MAKE) e2e-up && $(MAKE) e2e-run; ret=$$?; \
	if [ "$$was_up" = "false" ]; then $(MAKE) e2e-down; fi; \
	exit $$ret

local-up: ## Start full stack locally (no TLS, direct port access)
	$(LOCAL_COMPOSE) up -d --build
	@echo ""
	@echo "  REST API: http://localhost:15101/health"
	@echo "  VNC:      http://localhost:15100"
	@echo ""

local-down: ## Stop local stack
	$(LOCAL_COMPOSE) down

logs: ## Stream service logs (S=service ENV=local)
	@. ./.env 2>/dev/null; . ./.env.droplet 2>/dev/null; \
	env="$${BRIDGE_ENV:-$${DEFAULT_CLI_ENV:-prod}}"; \
	[ -n "$(ENV)" ] && env="$(ENV)"; \
	if [ "$$env" = "local" ]; then \
		$(LOCAL_COMPOSE) logs -f $(S); \
	else \
		[ -n "$$DROPLET_IP" ] || { echo "Error: DROPLET_IP not set — run 'make deploy' first"; exit 1; }; \
		ssh -i $${SSH_KEY:-~/.ssh/$(PROJECT)} root@$$DROPLET_IP \
			"cd /opt/$(PROJECT) && docker compose logs -f --tail=200 $(S)"; \
	fi

stats: ## Show container resource usage
	@. ./.env 2>/dev/null; . ./.env.droplet 2>/dev/null; \
	env="$${BRIDGE_ENV:-$${DEFAULT_CLI_ENV:-prod}}"; \
	[ -n "$(ENV)" ] && env="$(ENV)"; \
	if [ "$$env" = "local" ]; then \
		docker stats --no-stream $$($(LOCAL_COMPOSE) ps -q); \
	else \
		[ -n "$$DROPLET_IP" ] || { echo "Error: DROPLET_IP not set — run 'make deploy' first"; exit 1; }; \
		ssh -i $${SSH_KEY:-~/.ssh/$(PROJECT)} root@$$DROPLET_IP \
			"docker stats --no-stream"; \
	fi

gateway: ## Start IB Gateway container + show connection status
	@. ./.env; . ./.env.droplet 2>/dev/null; \
	[ -n "$$DROPLET_IP" ] || { echo "Error: DROPLET_IP not set — run 'make deploy' first"; exit 1; }; \
	ssh -i $${SSH_KEY:-~/.ssh/$(PROJECT)} root@$$DROPLET_IP \
		"cd /opt/$(PROJECT) && docker compose start ib-gateway && sleep 5 && curl -sf http://localhost:15101/health | python3 -m json.tool"

ssh: ## SSH into droplet
	@. ./.env; . ./.env.droplet 2>/dev/null; \
	[ -n "$$DROPLET_IP" ] || { echo "Error: DROPLET_IP not set — run 'make deploy' first"; exit 1; }; \
	ssh -i $${SSH_KEY:-~/.ssh/$(PROJECT)} root@$$DROPLET_IP
