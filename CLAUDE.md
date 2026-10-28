# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo of safe vs. unsafe changes to running Temporal workflows (Python SDK, managed with `uv`). The README walks through the demos and lists patching gotchas.

## Commands

`make help` lists everything. Common ones:
- `make install`; `make server-bg` / `make server-stop` / `make server-reset` / `make server-logs` (docker compose stack pinned to prod versions: server 1.31.2, UI 2.51.1; keep versions in `docker-compose.yaml` aligned with production); `make worker-bg` / `make restart-worker` / `make worker-stop` (or `make worker` in the foreground)
- `make start|ack|cancel|address|status|result ORDER_ID=1001` drive an order via `starter.py`
- `make test` (pytest; uses `WorkflowEnvironment.start_local()`, i.e. the CLI dev server, independent of the compose stack). Single test: `uv run pytest tests/test_order_workflow.py::test_unpatched_change_breaks_replay`
- `make seed-inline` (orders 5001-5003) / `seed-hoisted` (6001-6002) / `list-patches` / `status-all` / `reset-all` drive the multi-patch demo; the two lineages are not replay-compatible, so they use separate orders
- `make lint` runs pre-commit (Ruff check + format)

## Architecture

- **`workflows.py` is a generated/live copy.** The real sources are `scenarios/v1_baseline.py`, `v2_bad_unpatched.py`, `v2_good_patched.py`, `v3_deprecated.py`, plus the multi-patch set `v4_multi_inline.py`, `v4_multi_hoisted.py`, `v5_hoisted_deprecate_wrong.py`, `v5_hoisted_deprecate_right.py`, `v5_inline_deprecate.py` (v4 files are built from `v2_good_patched.py`; the hoisted v5 files derive from `v4_multi_hoisted.py`, `v5_inline_deprecate.py` from `v4_multi_inline.py`). `make scenario-*` copies one over `workflows.py`, and the worker then needs `make restart-worker`. Edit the scenario files, not `workflows.py`, and keep `workflows.py` equal to `v1_baseline.py` when committing. If you change the base workflow, update all the scenarios so the diffs between them stay limited to the intended change.
- `scenarios/` is a package so the tests can import each version and replay a captured v1 history against it (`tests/test_order_workflow.py`).
- `starter.py` addresses the workflow and its handlers by string name, so it works with any scenario. Workflow IDs are `order-<ORDER_ID>`; the task queue is `orders` (`models.TASK_QUEUE`).
- The compose stack has a one-shot `temporal-schema` job (Postgres schemas), the server (the image has no `temporal` CLI, so its healthcheck is a TCP check), a one-shot `temporal-namespace` job that registers `default`, and the UI. `dynamicconfig/docker.yaml` is mounted because the server image requires that file to exist.
- Activities keep state in an in-process dict and sleep a random `ACTIVITY_DELAY_MIN`..`ACTIVITY_DELAY_MAX` seconds per call (default 1..4; tests set both to 0).
- Runtime files (`.worker.*`) are gitignored.
