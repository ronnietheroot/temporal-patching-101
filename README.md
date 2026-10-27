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
| `v2_good_patched.py` | `scenario-v2-good` | **Proper step 1:** same change behind `workflow.patched("add-fraud-check")` |
| `v3_deprecated.py` | `scenario-v3` | **Proper step 2:** `workflow.deprecate_patch(...)` |

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

## Tests

`make test` runs behavior tests (signals, update validation, saga) and **replay tests** against a history captured from v1: v1 and v2-good replay cleanly, v2-bad raises a non-determinism error. Running `Replayer` against exported production histories in CI is how you catch this before deploying.

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
- This workflow waits up to 24h for the customer, so executions live long and patches stay around long. For large or incompatible changes, prefer a new workflow type (`OrderWorkflowV2`) for new orders, or Worker Versioning (PINNED / AUTO_UPGRADE).

**Operations**
- All workers on a task queue must run the same workflow code. Mixed fleets during a rollout cause flapping failures. In dev, stop the old worker before starting the new one.
- Non-determinism is a *workflow task* failure, not a workflow failure: the workflow stays Running and retries, and recovers once the code matches again. Don't terminate in production just because you see the error. Fix or revert the code first.
- Reverting only works if the workflow did not progress under the bad code. If it did, use `reset`.
- `reset` re-executes everything after the reset point, so activities like `update_inventory` and `ship_order` must be idempotent (this demo's in-memory ones are not).
- Don't run replay checks only by eye. Test with `Replayer` against real exported histories (`temporal workflow show --output json`) before deploying.
