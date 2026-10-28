# temporal-patching-101

A hands-on demo of **safe and unsafe changes to running Temporal workflows**, using an online-marketplace `OrderWorkflow` (Python SDK).

The workflow: `get_order` → `check_stock` → `check_inventory` → `update_inventory` → `ship_order` → wait for the customer to acknowledge.

- **Signals:** `acknowledge_order`, `cancel_order`
- **Update:** `change_shipping_address` (validator rejects it once shipped)
- **Query:** `get_status`
- **Saga compensation:** if shipping fails, `restore_inventory` puts the stock back

Prerequisites: Docker with Compose, the [`temporal` CLI](https://docs.temporal.io/cli) (used for `list`/`show`/`reset`), [`uv`](https://docs.astral.sh/uv/).

The server runs from `docker-compose.yaml`, pinned to the production versions (server `1.31.2`, UI `2.51.1`) with PostgreSQL persistence and visibility. History survives `make server-stop`; `make server-reset` wipes it. `make server-logs` tails the server. Run `make help` for every target.

## How the demo is wired

`workflows.py` is the **live** code the worker runs. `scenarios/` holds complete copies of it at each stage of the story; `make scenario-*` copies one over `workflows.py`, and `make restart-worker` makes the worker pick it up. Compare stages with e.g. `diff scenarios/v1_baseline.py scenarios/v2_good_patched.py`.

| Scenario | Make target | What it is |
|---|---|---|
| `v1_baseline.py` | `scenario-v1` | The original workflow. Also the "undo" target. |
| `v2_bad_unpatched.py` | `scenario-v2-bad` | **Improper:** inserts `check_fraud` mid-sequence with no patch |
| `v2_good_patched.py` | `scenario-v2-good` | **Proper step 1:** same change behind `workflow.patched("order-fraud-check")` |
| `v3_deprecated.py` | `scenario-v3` | **Proper step 2:** `workflow.deprecate_patch(...)` |

The multi-patch (`v4`, `v5`) and guide scenarios (`lifecycle_*`, `chain_*`, `anti_*`) are described in Demos 3 to 7; `make list-scenarios` prints them all.

Activities sleep a random 1 to 4 seconds per call (`ACTIVITY_DELAY_MIN` / `ACTIVITY_DELAY_MAX`) to mimic real API latency, so there is time to send signals/updates. A full run to `SHIPPED` takes roughly 5 to 20 seconds. Order IDs containing `oos` are out of stock; `shipfail` makes shipping fail; `fraud` fails the v2 fraud check.

## Setup

```bash
make install
make server-bg      # docker compose: Temporal server 1.31.2 + UI 2.51.1 + Postgres (matches prod); UI at http://localhost:8233
make scenario-v1
make worker-bg      # logs in .worker.log (or run `make worker` in its own terminal)
```

Happy path:

```bash
make start ORDER_ID=1001
make address ORDER_ID=1001      # within the first few seconds, before it ships
make status ORDER_ID=1001       # SHIPPED once parked awaiting acknowledgement (5-20s)
make ack ORDER_ID=1001          # or: make cancel ORDER_ID=1001
make result ORDER_ID=1001
```

## Demo 1: the improper change, and recovering from it

1. `make start ORDER_ID=2001` and wait until `make status ORDER_ID=2001` says `SHIPPED`. The order is now open, parked on the acknowledgement wait.
2. Deploy the bad change: `make scenario-v2-bad && make restart-worker`.
3. `make status ORDER_ID=2001` now fails:
   `Nondeterminism error: Activity type of scheduled event 'update_inventory' does not match activity type of activity command 'check_fraud'`.
   The history says `update_inventory` came third; the new code wants `check_fraud` there. The Web UI shows the same as a failing *workflow task*, but the workflow itself stays **Running**.
4. **Undo option A, revert the code:** `make scenario-v1 && make restart-worker`. The next workflow task replays cleanly and the workflow carries on. `make status` works again, and `make ack` completes it.
5. **Undo option B, reset:** if the workflow progressed under the new code (so reverting alone no longer matches its history), use `make reset WORKFLOW_ID=order-2001`. It rewinds to the last completed workflow task (`RESET_TYPE=FirstWorkflowTask` or `temporal workflow reset --event-id N` for other points) and re-executes from there with whatever code the worker is running now. See the gotchas on idempotency before using it on real activities.
6. Last resort, dev only: `make terminate WORKFLOW_ID=order-2001`.

## Demo 2: the proper way (patch, deprecate, remove)

1. **Old order exists:** `make scenario-v1 && make restart-worker && make start ORDER_ID=3001`, wait for `SHIPPED`.
2. **Step 1, patch:** `make scenario-v2-good && make restart-worker`. Order 3001 replays fine because its history has no marker, so `patched()` is `False` and the fraud check is skipped.
3. `make start ORDER_ID=3002`. It runs `check_fraud` and records a patch marker.
4. `make list-patched` shows 3002; `make list-unpatched` shows 3001 (and any other pre-patch orders).
5. `make ack ORDER_ID=3001; make ack ORDER_ID=3002`. Both complete.
6. **Step 2, deprecate:** once `make list-unpatched` is empty, `make scenario-v3 && make restart-worker`. `deprecate_patch` keeps marker-bearing histories replayable while dropping the old branch.
7. **Step 3, remove:** once no open execution carries the marker (`make list-patched` is empty), delete the `deprecate_patch(...)` line.

`make demo-bad` and `make demo-good` print these steps.

## Demo 3: multiple patches, inline vs saved in variables

More changes arrive, so more patches pile up. This demo has three: `order-fraud-check`, `order-notify-on-ship` (new `send_shipment_notification` after shipping) and `order-award-loyalty-points` (new `award_loyalty_points` after the customer acknowledges).

| Scenario | Make target | What it is |
|---|---|---|
| `v4_multi_inline.py` | `scenario-v4-multi-inline` | Each patch is an inline `if workflow.patched("...")` where the change happens |
| `v4_multi_hoisted.py` | `scenario-v4-multi-hoisted` | The same three patches saved as `fraud_check = workflow.patched(...)`, `notify = ...`, `loyalty = ...` at the top of `run`, then reused in several places (including a combined `notify and loyalty` condition) |
| `v5_inline_deprecate.py` | `scenario-v5-inline-deprecate` | The correct `deprecate_patch` for `order-fraud-check` with inline patches: one contiguous edit |
| `v5_hoisted_deprecate_wrong.py` | `scenario-v5-hoisted-wrong` | The tempting one-line cleanup: `fraud_check = True` |
| `v5_hoisted_deprecate_right.py` | `scenario-v5-hoisted-right` | The correct `deprecate_patch` for `order-fraud-check` only |

Two tracks tell the same story (seed open orders, inspect which patch markers they carry, deprecate `order-fraud-check`), one per style. They use separate order IDs because inline and hoisted histories cannot replay on each other's code (see below).

**3a. Inline patches (proper)**

1. `make seed-inline` opens `5001` (no markers), `5002` (fraud marker, from v2-good) and `5003` (all three markers), and leaves `v4_multi_inline` live.
2. `make list-patches` shows which open orders carry which marker. `make status-all` queries them (a query forces a replay), and all three are healthy.
3. `make scenario-v5-inline-deprecate && make restart-worker && make status-all`. The cleanup is **one contiguous edit** at the change site. `5002` and `5003` replay fine; `5001` (no marker) fails, so it must close (or be reset) before this deploy.

**3b. Patch results saved in variables (hoisted)**

1. Start clean (`make scenario-v1 && make server-reset && make server-bg`), then `make seed-hoisted` opens `6001` (no markers) and `6002` (three markers) with `v4_multi_hoisted` live.
2. Wrong cleanup: `make scenario-v5-hoisted-wrong && make restart-worker && make status-all`. Everything fails.
3. Revert (`make scenario-v4-multi-hoisted && make restart-worker`), then the right cleanup: `make scenario-v5-hoisted-right && make restart-worker`. `6002` replays, `6001` fails, and the edit is **three separate hunks** (`diff scenarios/v4_multi_hoisted.py scenarios/v5_hoisted_deprecate_right.py`).

`make reset-all` resets all seeded orders; `make demo-multi` prints both walkthroughs.

What the replay tests (`tests/test_order_workflow.py`) and the scenario diffs show:

- **Hoisting is behaviorally identical but not replay-compatible.** `patched()` records its marker at the point it is called. Hoisted, that is the first workflow task, not the change site. So inline code cannot replay histories recorded by hoisted code, and hoisted code cannot replay histories recorded by inline code (`non-deprecated patch marker encountered ... but there is no corresponding change command`). Turning inline patches into variables is itself a breaking change for open workflows.
- **Deprecating one hoisted patch touches every use site.** The inline cleanup is one hunk and the hoisted one is three (a test asserts both counts), and that is on a small example. You must replace the assignment with `deprecate_patch(...)` at the same position, rewrite each guarded call, find the second use far below, and untangle any combined condition (`notify and loyalty`). Miss one and you get a `NameError` or a silent behavior change.
- **The shortcut breaks everything.** Hardcoding the variable (`fraud_check = True`) fails replay for histories without the marker and for histories with it. Removing a `patched()` call is only legal as `deprecate_patch()`.
- **The right cleanup still has preconditions.** It replays only histories recorded by the hoisted code. Orders that predate the patch (`5001`) and orders from the inline lineage (`5002`) fail, so they must close (or be reset) first. With three patches there are already several populations to drain, in order.
- **Hoisted patches decide once.** An open order that has not reached a change site never adopts the new behavior, because the decision was made at the top. Inline patches would apply it.

## Demos 4 to 7: patterns and anti-patterns from the platform-core patching guide

These follow `bevaya/platform-core/documentation/temporal-workflow-changes-and-patching-guide.md`. The change used here is replacing `update_inventory` with `reserve_inventory` (patch `order-reserve-inventory`), then with `hold_inventory` (patch `order-hold-inventory`), plus a per-item `audit_item` (`order-item-audit`). `make list-scenarios` lists every scenario with a one-line description, and `make scenario NAME=<file>` deploys any of them. Each result below is asserted by a replay test in `tests/test_patterns.py`, not just described.

The tests replay *histories recorded under one version of the code on another*. A replay "fails" when the live worker would raise a non-determinism error for that order.

### Demo 4: the three-phase lifecycle

| Phase | Scenario | Make target |
|---|---|---|
| 1. Patch (keep the old branch) | `lifecycle_1_patch.py` | `scenario-lifecycle-1-patch` |
| 2. Deprecate (`deprecate_patch` + new path only) | `lifecycle_2_deprecate.py` | `scenario-lifecycle-2-deprecate` |
| 3. Remove (single path) | `lifecycle_3_remove.py` | `scenario-lifecycle-3-remove` |

`make seed-lifecycle` opens `7001` (legacy, no marker) and `7002` (phase-1 marker). Then:

1. Deploy phase 2 and `make status-all`: `7001` fails (`update_inventory` in history, `reserve_inventory` in code). Phase 2 is only safe once no open order took the old branch. `7002` replays.
2. Deploy phase 3: `7002` now fails with `Non-deprecated patch marker encountered ... but there is no corresponding change command`. **Skipping phase 2 breaks phase-1 markers**; histories recorded under phase 2 carry a *lenient* marker, so removing `deprecate_patch` is safe for them (tested).
3. `make retention-check` is the pre-flight for phase 2: namespace retention, open orders per patch ID and with no marker, and orders closed within `RETENTION_DAYS` (closed orders are still replayable until retention expires). Deploy phase 2 only when no open order lacks the marker **and** the retention window of the last old-path order has elapsed.

### Demo 5: chained `elif` versions (deprecate bottom-up)

`chain_1_all_versions.py` checks the newest patch first (`if patched(hold) ... elif patched(reserve) ... else legacy`). Then `chain_2_deprecate_oldest.py`, `chain_3_deprecate_next.py`, `chain_4_final.py` are the guide's steps 2 to 4. `make seed-chain` opens `8001` (legacy), `8002` (reserve era) and `8003` (hold era).

- Step 2 breaks the legacy order only. Step 3 deployed *top-down* (before the reserve era drains) breaks both older eras. Step 4 breaks every order recorded before step 3, because their markers are not deprecated yet.
- Each deprecation waits for the previous era to close **and** pass the retention window. Run `make retention-check` between steps.

### Demo 6: anti-patterns

| Anti-pattern | Scenario | What the tests show |
|---|---|---|
| Inverted patch (`if not workflow.patched(...)`) | `anti_inverted.py`, `anti_inverted_bad_deprecate.py` | Replays exactly like the proper form, so nothing flags it. The cost is reasoning: the guarded branch is the *old* code, and the natural cleanup (keep the body) deletes the new path and breaks every new order. |
| Nested patches | `anti_nested.py`, `anti_nested_deprecate_inner.py` | Three history populations (legacy / outer only / outer and inner). Deprecating the inner patch while outer-only orders are open sends them down the wrong path. `make seed-nested`. Use the flat `elif` chain instead. |
| `patched()` inside a loop | `anti_patch_in_loop.py` vs `loop_hoisted_ok.py` | Identical replay: `patched()` is memoized, so it is one decision made on the first iteration. The loop form only *suggests* a per-item decision; hoist the conditional. |
| Reusing a patch ID for a different change | `anti_patch_id_reuse.py` | Orders that already carry the marker take the new path "regardless of intent". An open order **silently runs the new activity** (`make seed-idreuse`, deploy, ack it, `make show WORKFLOW_ID=order-9201`); an order whose history already passed that point fails replay. |
| Ticket-style or mixed-case IDs (`squ-533`, `addApiUrl`) | n/a | `test_patch_ids_follow_the_naming_convention` rejects any ID that is not lowercase kebab-case with the `order-` workflow slug. |

`make demo-antipatterns` prints the walkthrough.

### Demo 7: rename/cutover and when not to patch

For rare, short or internal workflows, skip patching: ship the new behavior as a new workflow type and drain the old one.

1. `workflows_v2.py` holds `OrderWorkflowV2` (new behavior, no patches). `worker.py` registers it next to `OrderWorkflow`, so both types run side by side. `make seed-rename` opens `9101` (old type) and `9102` (V2); start new orders with `make start WORKFLOW_TYPE=OrderWorkflowV2 ORDER_ID=...`.
2. `make drain-check WORKFLOW_TYPE=OrderWorkflow` must come back empty (or terminate the stragglers).
3. Only then remove `OrderWorkflow` from the list in `worker.py` and delete the class. Done while old-type orders are open, they fail (`Workflow class OrderWorkflow is not registered on this worker`); `test_rename_cutover_needs_the_old_type_drained` replays both cases.

| Situation | Approach |
|---|---|
| Customer-facing, long-running or high-frequency workflow that may be in flight | `patched()` and the full lifecycle (Demos 2 and 4) |
| Rare, short or internal workflow | Rename to a new type, drain, delete the old type (Demo 7) |
| The workflow was never deployed in that environment | Delete the old path directly, no patch needed |
| Only an activity implementation changed, not the workflow's command sequence | No patch needed. Replay never runs activities (the replay tests register none), so in-flight orders are unaffected |

### Patch ID naming and registry

IDs are `<workflow-slug>-<feature-slug>`: lowercase kebab-case, descriptive of the change rather than the ticket, never reused for a different change in the same workflow type. Active IDs in this demo:

| Patch ID | Used in | Change |
|---|---|---|
| `order-fraud-check` | `v2` to `v5` | `check_fraud` before `update_inventory` |
| `order-notify-on-ship` | `v4`, `v5` | `send_shipment_notification` after shipping |
| `order-award-loyalty-points` | `v4`, `v5` | `award_loyalty_points` after acknowledgement |
| `order-reserve-inventory` | `lifecycle_*`, `chain_*`, `anti_*` | `reserve_inventory` replaces `update_inventory` |
| `order-hold-inventory` | `chain_*`, `anti_nested*` | `hold_inventory` replaces `reserve_inventory` |
| `order-item-audit` | `anti_patch_in_loop`, `loop_hoisted_ok` | per-item `audit_item` |

One more caveat the tests surfaced: replaying a history that is *parked* at the acknowledgement wait does not check commands the new code adds after that point. To test a change located after the last recorded workflow task, replay a completed history (`capture(..., completed=True)` in `tests/helpers.py`).

## Tests

`make test` runs behavior tests (signals, update validation, saga) and **replay tests** against histories captured from v1, v2-good and v4-hoisted: v1 and v2-good replay cleanly, v2-bad raises a non-determinism error, and the multi-patch matrix covers inline vs hoisted and the wrong/right cleanups. Running `Replayer` against exported production histories in CI is how you catch this before deploying.

## Patching gotchas & things to avoid

**What breaks replay**
- Anything that changes the sequence of *commands* for an existing history: adding, removing or reordering activities, timers, child workflows; renaming an activity or timer; swapping `sleep` for `wait_condition`, and the like.
- Picking a code path from anything non-deterministic: `datetime.now`, `random`, env vars, feature flags, config, or `workflow.unsafe.is_replaying()`. Use activities, `workflow.now()`, `workflow.random()`, or `patched()`.

**Generally safe without a patch**
- Changes inside activity implementations, activity timeouts and retry policies, logging, and adding new signal/update/query handlers. Changing handler bodies is safe only if the commands they issue stay the same.

**Patching process**
- It is three steps: `patched()`, then `deprecate_patch()`, then remove. Skipping steps, or deprecating or removing while pre-patch (or marker-bearing) executions are still open, brings non-determinism back. Check with `TemporalChangeVersion` list queries (`make list-patched` / `make list-unpatched`) before each step.
- Put `patched()` exactly where the change takes effect, and keep the old branch identical to the original code.
- Patch IDs must be descriptive and unique, and never reused for a different change.
- In Python, `patched()` is memoized, so it is not reliable inside loops. Suffix the ID per iteration (`f"change-{i}"`).
- This workflow waits indefinitely for the customer (no ack timeout), so executions live long and patches stay around long. For large or incompatible changes, prefer a new workflow type (`OrderWorkflowV2`) for new orders, or Worker Versioning (PINNED / AUTO_UPGRADE).

**Operations**
- All workers on a task queue must run the same workflow code. Mixed fleets during a rollout cause flapping failures. In dev, stop the old worker before starting the new one.
- Non-determinism is a *workflow task* failure, not a workflow failure: the workflow stays Running and retries, and recovers once the code matches again. Don't terminate in production just because you see the error. Fix or revert the code first.
- Reverting only works if the workflow did not progress under the bad code. If it did, use `reset`.
- `reset` re-executes everything after the reset point, so activities like `update_inventory` and `ship_order` must be idempotent (this demo's in-memory ones are not).
- Don't save `patched()` in a variable (see Demo 3). If you must, keep it 1:1 with one change site, never refactor between inline and hoisted while workflows are open, and deprecate patches in the order they were introduced.
- Every patch adds a history population (no marker, marker 1, markers 1+2, ...). Keep the list of live patch IDs short, and use `make list-patches` before each deprecation.
- Call `patched()` inline in the condition, before any `await` that could branch differently, so the marker lands at a deterministic point. See Demos 4 to 7 for the lifecycle, `elif` chains, and the anti-patterns (inverted, nested, in a loop, reused IDs).
- Don't run replay checks only by eye. Test with `Replayer` against real exported histories (`temporal workflow show --output json`) before deploying.
