.DEFAULT_GOAL := help

ORDER_ID    ?= 1001
WORKFLOW_ID ?= order-$(ORDER_ID)
ADDRESS     ?= 500 Howard St, San Francisco, CA
RESET_TYPE  ?= LastWorkflowTask
PATCH_ID    ?= order-fraud-check
PATCH_IDS   ?= order-fraud-check order-notify-on-ship order-award-loyalty-points \
               order-reserve-inventory order-hold-inventory order-item-audit
WORKFLOW_TYPE ?= OrderWorkflow
RETENTION_DAYS ?= 3
NAME        ?=
SEED_INLINE  ?= 5001 5002 5003
SEED_HOISTED ?= 6001 6002
SEED_LIFECYCLE ?= 7001 7002
SEED_CHAIN   ?= 8001 8002 8003
SEED_NESTED  ?= 9001 9002 9003
SEED_RENAME  ?= 9101 9102
SEED_IDREUSE ?= 9201
SEED_ALL     ?= $(SEED_INLINE) $(SEED_HOISTED) $(SEED_LIFECYCLE) $(SEED_CHAIN) $(SEED_NESTED) $(SEED_RENAME) $(SEED_IDREUSE)
ORDERS       ?= $(SEED_ALL)

# Scenario files from the patching guide (patterns and anti-patterns); each gets a scenario-<name> target.
GUIDE_SCENARIOS := lifecycle_1_patch lifecycle_2_deprecate lifecycle_3_remove \
        chain_1_all_versions chain_2_deprecate_oldest chain_3_deprecate_next chain_4_final \
        anti_inverted anti_inverted_bad_deprecate anti_nested anti_nested_deprecate_inner \
        anti_patch_in_loop loop_hoisted_ok anti_patch_id_reuse
GUIDE_TARGETS := $(addprefix scenario-,$(subst _,-,$(GUIDE_SCENARIOS)))

PY          := .venv/bin/python
WORKER_PID  := .worker.pid
WORKER_LOG  := .worker.log

.PHONY: help install server server-bg server-stop server-reset server-logs worker worker-bg worker-stop restart-worker \
        start ack cancel address status result list show \
        scenario-v1 scenario-v2-bad scenario-v2-good scenario-v3 \
        scenario-v4-multi-inline scenario-v4-multi-hoisted scenario-v5-hoisted-wrong scenario-v5-hoisted-right scenario-v5-inline-deprecate \
        list-patched list-unpatched list-patches seed-inline seed-hoisted status-all reset reset-all terminate \
        test lint demo-bad demo-good demo-multi demo-patterns demo-antipatterns \
        scenario list-scenarios seed seed-lifecycle seed-chain seed-nested seed-rename seed-idreuse retention-check drain-check $(GUIDE_TARGETS)

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
start: ## Start a workflow for ORDER_ID (WORKFLOW_TYPE=OrderWorkflow|OrderWorkflowV2)
	$(PY) starter.py start $(ORDER_ID) --type $(WORKFLOW_TYPE)

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

scenario-v4-multi-inline: ## Live workflows.py := 3 patches, each inline where the change happens
	cp scenarios/v4_multi_inline.py workflows.py
	@echo ">> workflows.py is now v4 (3 inline patches). Run 'make restart-worker'."

scenario-v4-multi-hoisted: ## Live workflows.py := same 3 patches saved in variables at the top of run()
	cp scenarios/v4_multi_hoisted.py workflows.py
	@echo ">> workflows.py is now v4 (3 HOISTED patch variables). Run 'make restart-worker'."

scenario-v5-inline-deprecate: ## Live workflows.py := correct deprecation of order-fraud-check with INLINE patches
	cp scenarios/v5_inline_deprecate.py workflows.py
	@echo ">> workflows.py is now v5 inline deprecate. Safe only once no open order lacks the marker. Run 'make restart-worker'."

scenario-v5-hoisted-wrong: ## Live workflows.py := WRONG cleanup of a hoisted patch (fraud_check = True)
	cp scenarios/v5_hoisted_deprecate_wrong.py workflows.py
	@echo ">> workflows.py is now v5-WRONG. Run 'make restart-worker' and run 'make status-all'."

scenario-v5-hoisted-right: ## Live workflows.py := correct deprecation of the hoisted order-fraud-check patch
	cp scenarios/v5_hoisted_deprecate_right.py workflows.py
	@echo ">> workflows.py is now v5-RIGHT. Only safe once 'make list-unpatched' and inline-lineage orders are gone."

# ---- Guide scenarios (patterns and anti-patterns) -------------------------------------------
define guide_scenario
scenario-$(subst _,-,$(1)):
	cp scenarios/$(1).py workflows.py
	@echo ">> workflows.py is now $(1). Run 'make restart-worker'."
endef
$(foreach s,$(GUIDE_SCENARIOS),$(eval $(call guide_scenario,$(s))))

scenario: ## Live workflows.py := scenarios/$(NAME).py (see list-scenarios)
	@test -f scenarios/$(NAME).py || { echo "usage: make scenario NAME=<file in scenarios/ without .py>"; exit 1; }
	cp scenarios/$(NAME).py workflows.py
	@echo ">> workflows.py is now $(NAME). Run 'make restart-worker'."

list-scenarios: ## List every scenario file with its one-line description
	@for f in scenarios/[a-z]*.py; do \
		name=$$(basename $$f .py); \
		printf "  %-30s %s\n" "$$name" "$$(head -1 $$f | sed 's/^"""//; s/"""$$//')"; \
	done

# ---- Patch bookkeeping & recovery ----------------------------------------------------------
list-patched: ## Open orders that have the patch marker (PATCH_ID=...)
	temporal workflow list --query 'WorkflowType = "OrderWorkflow" AND ExecutionStatus = "Running" AND TemporalChangeVersion = "$(PATCH_ID)"'

list-unpatched: ## Open orders WITHOUT any patch marker (must be empty before deprecating)
	temporal workflow list --query 'WorkflowType = "OrderWorkflow" AND ExecutionStatus = "Running" AND TemporalChangeVersion IS NULL'

list-patches: ## Show open orders per patch ID (PATCH_IDS=...) and those with no marker
	@for id in $(PATCH_IDS); do \
		echo "== $$id"; \
		temporal workflow list --query "WorkflowType = \"OrderWorkflow\" AND ExecutionStatus = \"Running\" AND TemporalChangeVersion = \"$$id\"" 2>&1 | tail -n +2; \
	done; \
	echo "== (no patch marker)"; \
	temporal workflow list --query 'WorkflowType = "OrderWorkflow" AND ExecutionStatus = "Running" AND TemporalChangeVersion IS NULL' 2>&1 | tail -n +2

define seed_orders
	@set -e; \
	for entry in $(1); do \
		scenario=$${entry%%:*}; rest=$${entry#*:}; id=$${rest%%:*}; type=$${rest#*:}; \
		[ "$$type" = "$$rest" ] && type=OrderWorkflow; \
		$(MAKE) --no-print-directory $$scenario restart-worker >/dev/null; \
		$(PY) starter.py start $$id --type $$type; \
		for i in $$(seq 1 60); do \
			$(PY) starter.py status $$id 2>/dev/null | grep -q '^SHIPPED' && break; sleep 1; \
		done; \
		echo "order $$id parked awaiting acknowledgement"; \
	done
endef

seed-inline: ## Inline track: 5001 (v1, no marker), 5002 (v2-good), 5003 (v4-inline); leaves v4-inline live
	$(call seed_orders,"scenario-v1:5001" "scenario-v2-good:5002" "scenario-v4-multi-inline:5003")
	@echo "Seeded 5001=no markers, 5002=fraud marker, 5003=3 markers (all inline). Live: v4-inline."

seed-hoisted: ## Hoisted track: 6001 (v1, no marker), 6002 (v4-hoisted); leaves v4-hoisted live
	$(call seed_orders,"scenario-v1:6001" "scenario-v4-multi-hoisted:6002")
	@echo "Seeded 6001=no markers, 6002=3 markers (hoisted). Live: v4-hoisted."

seed: ## Generic seeding: make seed SEED="scenario-v1:9901 scenario-v2-good:9902[:WorkflowType]"
	$(call seed_orders,$(SEED))

seed-lifecycle: ## Lifecycle: 7001 (legacy), 7002 (phase-1 marker); leaves phase 1 live
	$(call seed_orders,"scenario-v1:7001" "scenario-lifecycle-1-patch:7002")

seed-chain: ## Chain: 8001 (legacy), 8002 (reserve era), 8003 (hold era); leaves chain step 1 live
	$(call seed_orders,"scenario-v1:8001" "scenario-lifecycle-1-patch:8002" "scenario-chain-1-all-versions:8003")

seed-nested: ## Nested: 9001 (legacy), 9002 (outer only), 9003 (outer+inner); leaves anti_nested live
	$(call seed_orders,"scenario-v1:9001" "scenario-lifecycle-1-patch:9002" "scenario-anti-nested:9003")

seed-rename: ## Rename/cutover: 9101 (OrderWorkflow), 9102 (OrderWorkflowV2); both types run side by side
	$(call seed_orders,"scenario-v1:9101:OrderWorkflow" "scenario-v1:9102:OrderWorkflowV2")

seed-idreuse: ## ID reuse: 9201 (reserve marker), parked; deploy anti_patch_id_reuse, ack it, inspect its history
	$(call seed_orders,"scenario-lifecycle-1-patch:9201")

status-all: ## Query the seeded orders (ORDERS=...); shows which fail replay after a deploy
	@for id in $(ORDERS); do \
		printf "order %s: " $$id; \
		$(PY) starter.py status $$id 2>&1 | tail -1 | cut -c1-200; \
	done

reset: ## Reset WORKFLOW_ID to RESET_TYPE (LastWorkflowTask|FirstWorkflowTask) -- re-runs later activities!
	temporal workflow reset --workflow-id $(WORKFLOW_ID) --type $(RESET_TYPE) --reason "recover from non-determinism"

reset-all: ## Reset the seeded orders to RESET_TYPE (re-runs later activities!)
	@for id in $(SEED_ALL); do $(MAKE) --no-print-directory reset WORKFLOW_ID=order-$$id || true; done

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

demo-multi: ## Print the multiple-patches walkthrough (inline track, then hoisted track)
	@echo "== Track A: inline patches (proper) =="
	@echo "1. make server-bg && make seed-inline      # 5001 no marker / 5002 fraud marker / 5003 three markers"
	@echo "2. make list-patches ; make status-all     # all healthy under v4-inline"
	@echo "3. make scenario-v5-inline-deprecate restart-worker ; make status-all"
	@echo "   -> ONE contiguous edit; 5002 and 5003 fine, 5001 (no marker) fails: drain it first"
	@echo "== Track B: patch results saved in variables (hoisted) =="
	@echo "4. make scenario-v1 && make server-reset && make server-bg   # clean slate: inline-lineage orders can't replay on hoisted code"
	@echo "   make seed-hoisted                        # 6001 no marker / 6002 three markers"
	@echo "5. WRONG cleanup:  make scenario-v5-hoisted-wrong restart-worker ; make status-all   # all fail"
	@echo "6. revert, then RIGHT cleanup: make scenario-v4-multi-hoisted restart-worker; make scenario-v5-hoisted-right restart-worker"
	@echo "   -> 6002 fine, 6001 fails; diff scenarios/v4_multi_hoisted.py scenarios/v5_hoisted_deprecate_right.py (3 scattered hunks)"
	@echo "Inline and hoisted lineages are NOT replay-compatible, hence separate order ids (5xxx / 6xxx)."
	@echo "Cleanup: make server-reset"

retention-check: ## Phase-2 readiness: retention, open orders per patch / no marker, recently closed orders
	@echo "== namespace retention"; \
	temporal operator namespace describe -n default 2>&1 | grep -i retention || true
	@$(MAKE) --no-print-directory list-patches
	@cutoff=$$(python3 -c "import datetime as d; print((d.datetime.now(d.timezone.utc)-d.timedelta(days=$(RETENTION_DAYS))).strftime('%Y-%m-%dT%H:%M:%SZ'))"); \
	echo "== $(WORKFLOW_TYPE) closed since $$cutoff (RETENTION_DAYS=$(RETENTION_DAYS); still replayable)"; \
	temporal workflow list --query "WorkflowType = \"$(WORKFLOW_TYPE)\" AND ExecutionStatus != \"Running\" AND CloseTime > \"$$cutoff\"" 2>&1 | tail -n +2
	@echo "== Deploy phase 2 (deprecate_patch) only when: 1) no open order lacks the marker, 2) the retention window for the last old-path order has elapsed."

drain-check: ## Open executions of WORKFLOW_TYPE (must be empty before deleting that workflow class)
	temporal workflow list --query 'WorkflowType = "$(WORKFLOW_TYPE)" AND ExecutionStatus = "Running"'

demo-patterns: ## Print the guide's pattern walkthroughs (lifecycle, elif chain, rename/cutover)
	@echo "== Lifecycle (patch -> deprecate -> remove) =="
	@echo "1. make server-bg && make seed-lifecycle      # 7001 legacy, 7002 phase-1 marker; phase 1 live"
	@echo "2. make scenario-lifecycle-2-deprecate restart-worker; make status-all   # 7001 FAILS: drain legacy first (make ack ORDER_ID=7001)"
	@echo "3. make scenario-lifecycle-3-remove restart-worker; make status-all      # 7002 FAILS: skipping phase 2 breaks phase-1 markers"
	@echo "== Chain (bottom-up) =="
	@echo "4. make server-reset && make server-bg && make seed-chain               # 8001 legacy, 8002 reserve era, 8003 hold era"
	@echo "5. make scenario-chain-3-deprecate-next restart-worker; make status-all  # top-down: 8001, 8002 FAIL"
	@echo "   proper order: ack 8001, make scenario-chain-2-deprecate-oldest, ack 8002, then chain-3, then chain-4"
	@echo "== Rename/cutover =="
	@echo "6. make seed-rename                            # 9101 OrderWorkflow, 9102 OrderWorkflowV2"
	@echo "   make drain-check WORKFLOW_TYPE=OrderWorkflow ; make ack ORDER_ID=9101"
	@echo "   then, only once drain-check is empty, remove OrderWorkflow from worker.py's workflows list and delete the class"
	@echo "Always: make retention-check before a deprecate step."

demo-antipatterns: ## Print the guide's anti-pattern walkthroughs
	@echo "1. Inverted:  make scenario-anti-inverted (replays fine, but read it);  anti-inverted-bad-deprecate breaks new orders"
	@echo "2. Nested:    make server-reset && make server-bg && make seed-nested; make scenario-anti-nested-deprecate-inner restart-worker; make status-all   # 9002 FAILS"
	@echo "3. In a loop: make scenario-anti-patch-in-loop vs scenario-loop-hoisted-ok: identical replay; the loop form misleads readers"
	@echo "4. ID reuse:  make seed-idreuse; make scenario-anti-patch-id-reuse restart-worker; make ack ORDER_ID=9201; make show WORKFLOW_ID=order-9201"
	@echo "   (an open order does NOT fail: it silently runs send_shipment_notification it never had. Orders whose history already"
	@echo "    passed that point fail replay: see tests/test_patterns.py)"
	@echo "5. Bad IDs (ticket numbers, mixed case): rejected by tests/test_patterns.py naming test"
