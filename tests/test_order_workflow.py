"""Behavior tests (v1) and replay tests that show why unpatched changes break.

The replay tests run v1 on a real dev server until the workflow is parked waiting for the
customer, capture the history, then replay it against each scenario's code.
"""

import uuid

import pytest
from temporalio.client import Client, WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker, WorkflowReplayResult

import activities
from models import TASK_QUEUE
from scenarios import (
    v1_baseline,
    v2_bad_unpatched,
    v2_good_patched,
    v3_deprecated,
    v4_multi_hoisted,
    v4_multi_inline,
    v5_hoisted_deprecate_right,
    v5_hoisted_deprecate_wrong,
    v5_inline_deprecate,
)

ALL_ACTIVITIES = [
    activities.get_order,
    activities.check_stock,
    activities.check_inventory,
    activities.check_fraud,
    activities.update_inventory,
    activities.restore_inventory,
    activities.ship_order,
    activities.send_shipment_notification,
    activities.award_loyalty_points,
]


@pytest.fixture(autouse=True)
def fast_activities(monkeypatch):
    monkeypatch.setenv("ACTIVITY_DELAY_MIN", "0")
    monkeypatch.setenv("ACTIVITY_DELAY_MAX", "0")


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


def worker_for(client: Client, workflow_cls) -> Worker:
    from concurrent.futures import ThreadPoolExecutor

    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[workflow_cls],
        activities=ALL_ACTIVITIES,
        activity_executor=ThreadPoolExecutor(5),
    )


async def wait_for_status(handle, status: str) -> None:
    import asyncio

    for _ in range(100):
        if (await handle.query("get_status")).startswith(status):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached {status}")


async def test_ack_completes_order(env):
    async with worker_for(env.client, v1_baseline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "t1", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        await handle.signal("acknowledge_order")
        assert "delivered and acknowledged" in await handle.result()


async def test_cancel(env):
    async with worker_for(env.client, v1_baseline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "t2", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        await handle.signal("cancel_order")
        assert "cancelled" in await handle.result()


async def test_address_update_rejected_after_ship(env):
    async with worker_for(env.client, v1_baseline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "t3", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        with pytest.raises(WorkflowUpdateFailedError) as exc:
            await handle.execute_update("change_shipping_address", "somewhere else")
        assert "Cannot change address" in str(exc.value.cause)


async def test_shipping_failure_restores_inventory(env):
    before = {w: dict(s) for w, s in activities.INVENTORY.items()}
    async with worker_for(env.client, v1_baseline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "shipfail-1", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert activities.INVENTORY == before


@pytest.fixture
async def v1_history(env):
    """History of a v1 order parked at the customer-acknowledgement wait."""
    async with worker_for(env.client, v1_baseline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "replay-1", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        return await handle.fetch_history()


async def replay(workflow_cls, history) -> WorkflowReplayResult:
    return await Replayer(workflows=[workflow_cls]).replay_workflow(
        history, raise_on_replay_failure=False
    )


async def test_replay_v1_history_with_v1_is_fine(v1_history):
    assert (await replay(v1_baseline.OrderWorkflow, v1_history)).replay_failure is None


async def test_unpatched_change_breaks_replay(v1_history):
    result = await replay(v2_bad_unpatched.OrderWorkflow, v1_history)
    assert result.replay_failure is not None
    assert "Nondeterminism" in type(result.replay_failure).__name__


async def test_patched_change_replays_old_history(v1_history):
    assert (await replay(v2_good_patched.OrderWorkflow, v1_history)).replay_failure is None


async def test_deprecate_patch_still_replays_v1_history_for_demo_only(v1_history):
    """v3 is only safe once no pre-patch executions are open; v1 histories (no marker) fail."""
    result = await replay(v3_deprecated.OrderWorkflow, v1_history)
    assert result.replay_failure is not None


@pytest.fixture
async def v2_history(env):
    async with worker_for(env.client, v2_good_patched.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "replay-2", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        return await handle.fetch_history()


async def test_deprecated_patch_replays_marker_history(v2_history):
    assert (await replay(v3_deprecated.OrderWorkflow, v2_history)).replay_failure is None


@pytest.fixture
async def v4_history(env):
    """History of an order started under the hoisted three-patch workflow (all markers)."""
    async with worker_for(env.client, v4_multi_hoisted.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "replay-4", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        return await handle.fetch_history()


async def test_inline_and_hoisted_styles_are_not_interchangeable(
    v1_history, v2_history, v4_history
):
    """Hoisting moves the marker to the first workflow task, so switching style is itself breaking.

    Histories from the inline lineage (v1, v2) replay on inline code; histories recorded by the
    hoisted code (v4) replay only on hoisted code. v1 (no markers) is safe either way.
    """
    inline, hoisted = v4_multi_inline.OrderWorkflow, v4_multi_hoisted.OrderWorkflow
    assert (await replay(inline, v1_history)).replay_failure is None
    assert (await replay(inline, v2_history)).replay_failure is None
    assert (await replay(inline, v4_history)).replay_failure is not None
    assert (await replay(hoisted, v1_history)).replay_failure is None
    assert (await replay(hoisted, v2_history)).replay_failure is not None
    assert (await replay(hoisted, v4_history)).replay_failure is None


async def test_hoisted_wrong_cleanup_breaks_every_population(v1_history, v2_history, v4_history):
    """Hardcoding the variable breaks unmarked histories (extra command) and marker-bearing
    ones ("non-deprecated patch marker ... no corresponding change command")."""
    wrong = v5_hoisted_deprecate_wrong.OrderWorkflow
    for history in (v1_history, v2_history, v4_history):
        result = await replay(wrong, history)
        assert "Nondeterminism" in type(result.replay_failure).__name__


async def test_hoisted_right_cleanup_only_fits_the_hoisted_lineage(
    v1_history, v2_history, v4_history
):
    right = v5_hoisted_deprecate_right.OrderWorkflow
    # Pre-patch order still open: must be closed (or reset) before deprecating.
    assert (await replay(right, v1_history)).replay_failure is not None
    # Marker recorded mid-flow by the inline v2 code: no longer lines up with a hoisted call.
    assert (await replay(right, v2_history)).replay_failure is not None
    # Histories recorded by the hoisted code are the ones this cleanup is safe for.
    assert (await replay(right, v4_history)).replay_failure is None


def test_right_cleanup_keeps_remaining_patches():
    import inspect

    src = inspect.getsource(v5_hoisted_deprecate_right)
    assert 'deprecate_patch("add-fraud-check")' in src
    assert 'patched("add-fraud-check")' not in src
    assert 'patched("notify-on-ship")' in src and 'patched("award-loyalty-points")' in src


@pytest.fixture
async def v4_inline_history(env):
    """History of an order started under the inline three-patch workflow (all markers)."""
    async with worker_for(env.client, v4_multi_inline.OrderWorkflow):
        handle = await env.client.start_workflow(
            "OrderWorkflow", "replay-4i", id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        return await handle.fetch_history()


async def test_inline_lineage_replays_its_own_three_marker_history(v4_inline_history):
    assert (await replay(v4_multi_inline.OrderWorkflow, v4_inline_history)).replay_failure is None
    assert (
        await replay(v4_multi_hoisted.OrderWorkflow, v4_inline_history)
    ).replay_failure is not None


async def test_inline_deprecation_needs_unmarked_population_closed(
    v1_history, v2_history, v4_inline_history
):
    deprecate = v5_inline_deprecate.OrderWorkflow
    assert (await replay(deprecate, v1_history)).replay_failure is not None
    assert (await replay(deprecate, v2_history)).replay_failure is None
    assert (await replay(deprecate, v4_inline_history)).replay_failure is None


def _hunks(before, after) -> int:
    """Number of separate changed regions in the code (docstring excluded) of two scenarios."""
    import difflib
    import inspect

    def code(module):
        src = inspect.getsource(module)
        return src[src.index("from datetime") :].splitlines()

    ops = difflib.SequenceMatcher(None, code(before), code(after)).get_opcodes()
    return sum(1 for tag, *_ in ops if tag != "equal")


def test_inline_deprecation_is_one_hunk_hoisted_is_scattered():
    assert _hunks(v4_multi_inline, v5_inline_deprecate) == 1
    assert _hunks(v4_multi_hoisted, v5_hoisted_deprecate_right) == 3
