# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This repo is an early scaffold for a Temporal (Python SDK) patching/versioning tutorial. `workflows.py`, `activities.py`, `worker.py` and `Makefile` exist but are currently empty, and there is no `pyproject.toml`/`requirements.txt` yet. Update this file once real code, dependencies and make targets land.

## Intended layout

Standard Temporal Python split, one module each:
- `workflows.py` — workflow definitions (must stay deterministic; this is where patching/versioning changes go)
- `activities.py` — activity implementations
- `worker.py` — worker that registers the workflows and activities

## Tooling

- Lint/format via pre-commit with Ruff (`.pre-commit-config.yaml`): `ruff-check --fix` and `ruff-format`. Run with `pre-commit run --all-files`.
