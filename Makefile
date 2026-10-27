.DEFAULT_GOAL := help

ORDER_ID    ?= 1001
WORKFLOW_ID ?= order-$(ORDER_ID)
ADDRESS     ?= 500 Howard St, San Francisco, CA
RESET_TYPE  ?= LastWorkflowTask
PATCH_ID    ?= add-fraud-check

PY          := .venv/bin/python
WORKER_PID  := .worker.pid
WORKER_LOG  := .worker.log

.PHONY: help install server server-bg server-stop server-reset server-logs worker worker-bg worker-stop restart-worker \
        start ack cancel address status result list show \
        scenario-v1 scenario-v2-bad scenario-v2-good scenario-v3 \
        list-patched list-unpatched reset terminate test lint demo-bad demo-good

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

# ---- Temporal server (docker compose: server 1.31.2, UI 2.51.1, Postgres) ------------------
server: ## Run the Temporal stack in the foreground (UI: http://localhost:8233)
	docker compose up

server-bg: ## Start the Temporal stack in the background and wait until healthy
	docker compose up -d --wait temporal-ui
	@echo "Temporal ready: localhost:7233, UI: http://localhost:8233"

server-stop: ## Stop the stack (workflow history is kept in the postgres volume)
	docker compose down

server-reset: ## Stop the stack AND delete all workflow history (postgres volume)
	docker compose down -v

server-logs: ## Tail the Temporal server logs
	docker compose logs -f temporal

# ---- Worker --------------------------------------------------------------------------------
worker: ## Run the worker in the foreground (runs whatever is in workflows.py)
	uv run python worker.py

worker-bg: install ## Run the worker in the background (logs: .worker.log)
	@if [ -f $(WORKER_PID) ] && kill -0 $$(cat $(WORKER_PID)) 2>/dev/null; then \
		echo "Worker already running (pid $$(cat $(WORKER_PID)))"; \
	else \
		nohup $(PY) worker.py > $(WORKER_LOG) 2>&1 & echo $$! > $(WORKER_PID); \
		sleep 2; echo "Worker started (pid $$(cat $(WORKER_PID)))"; \
	fi

worker-stop: ## Stop the background worker
	@if [ -f $(WORKER_PID) ]; then kill $$(cat $(WORKER_PID)) 2>/dev/null; rm -f $(WORKER_PID); echo "Worker stopped"; \
	else echo "No $(WORKER_PID) found"; fi

restart-worker: worker-stop worker-bg ## Restart the background worker (picks up a new workflows.py)

# ---- Drive an order (ORDER_ID=1001) --------------------------------------------------------
start: ## Start an OrderWorkflow for ORDER_ID
	$(PY) starter.py start $(ORDER_ID)

ack: ## Customer acknowledges the order (signal)
	$(PY) starter.py ack $(ORDER_ID)

cancel: ## Customer cancels the order (signal)
	$(PY) starter.py cancel $(ORDER_ID)

address: ## Change shipping address before it ships (update); ADDRESS="..."
	$(PY) starter.py address $(ORDER_ID) "$(ADDRESS)"

status: ## Query the order status
	$(PY) starter.py status $(ORDER_ID)

result: ## Wait for and print the workflow result
	$(PY) starter.py result $(ORDER_ID)

list: ## List OrderWorkflow executions
	temporal workflow list --query 'WorkflowType = "OrderWorkflow"'

show: ## Show event history for WORKFLOW_ID
	temporal workflow show --workflow-id $(WORKFLOW_ID)

# ---- Swap the live workflow code (then run `make restart-worker`) ---------------------------
scenario-v1: ## Live workflows.py := v1 baseline (also the "undo" for a bad deploy)
	cp scenarios/v1_baseline.py workflows.py
	@echo ">> workflows.py is now v1 (baseline). Run 'make restart-worker'."

scenario-v2-bad: ## Live workflows.py := v2 IMPROPER change (unpatched new activity)
	cp scenarios/v2_bad_unpatched.py workflows.py
	@echo ">> workflows.py is now v2-BAD. Run 'make restart-worker' and watch in-flight orders break."

scenario-v2-good: ## Live workflows.py := v2 PROPER change (workflow.patched)
	cp scenarios/v2_good_patched.py workflows.py
	@echo ">> workflows.py is now v2-GOOD (patched). Run 'make restart-worker'."

scenario-v3: ## Live workflows.py := v3 (deprecate_patch)
	cp scenarios/v3_deprecated.py workflows.py
	@echo ">> workflows.py is now v3 (deprecate_patch). Only safe once no pre-patch orders are open."

# ---- Patch bookkeeping & recovery ----------------------------------------------------------
list-patched: ## Open orders that have the patch marker (PATCH_ID=...)
	temporal workflow list --query 'WorkflowType = "OrderWorkflow" AND ExecutionStatus = "Running" AND TemporalChangeVersion = "$(PATCH_ID)"'

list-unpatched: ## Open orders WITHOUT any patch marker (must be empty before deprecating)
	temporal workflow list --query 'WorkflowType = "OrderWorkflow" AND ExecutionStatus = "Running" AND TemporalChangeVersion IS NULL'

reset: ## Reset WORKFLOW_ID to RESET_TYPE (LastWorkflowTask|FirstWorkflowTask) -- re-runs later activities!
	temporal workflow reset --workflow-id $(WORKFLOW_ID) --type $(RESET_TYPE) --reason "recover from non-determinism"

terminate: ## Dev only: terminate WORKFLOW_ID
	temporal workflow terminate --workflow-id $(WORKFLOW_ID) --reason "demo cleanup"

# ---- Quality -------------------------------------------------------------------------------
test: ## Run unit + replay tests
	uv run pytest -q

lint: ## Run pre-commit (ruff)
	pre-commit run --all-files

# ---- Scripts -------------------------------------------------------------------------------
demo-bad: ## Print the "improper change + recovery" walkthrough
	@echo "1. make server-bg && make scenario-v1 && make worker-bg"
	@echo "2. make start ORDER_ID=2001   # wait until status is SHIPPED (parked awaiting ack)"
	@echo "3. make scenario-v2-bad && make restart-worker"
	@echo "4. make status ORDER_ID=2001  # query forces replay -> fails; see Web UI / 'make show WORKFLOW_ID=order-2001'"
	@echo "5. UNDO:  make scenario-v1 && make restart-worker   # workflow task retries and recovers"
	@echo "   RESET: make reset WORKFLOW_ID=order-2001         # when revert alone is not enough"
	@echo "6. make ack ORDER_ID=2001 && make result ORDER_ID=2001"

demo-good: ## Print the "proper patching" walkthrough
	@echo "1. make scenario-v1 && make restart-worker && make start ORDER_ID=3001   # old order, wait for SHIPPED"
	@echo "2. make scenario-v2-good && make restart-worker"
	@echo "3. make start ORDER_ID=3002   # new order runs check_fraud and records the patch marker"
	@echo "4. make list-patched ; make list-unpatched   # 3002 patched, 3001 not"
	@echo "5. make ack ORDER_ID=3001 && make ack ORDER_ID=3002   # both complete"
	@echo "6. make list-unpatched is empty -> make scenario-v3 && make restart-worker (deprecate_patch)"
	@echo "7. once no marker-bearing orders remain open, delete deprecate_patch() (step 3)"
