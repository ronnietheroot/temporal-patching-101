"""Shared helpers: run a scenario's workflow to the customer wait, capture its history, replay it."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Replayer, Worker, WorkflowReplayResult

import activities
from models import TASK_QUEUE

ALL_ACTIVITIES = [
    activities.get_order,
    activities.check_stock,
    activities.check_inventory,
    activities.check_fraud,
    activities.update_inventory,
    activities.reserve_inventory,
    activities.hold_inventory,
    activities.audit_item,
    activities.restore_inventory,
    activities.ship_order,
    activities.send_shipment_notification,
    activities.award_loyalty_points,
]


def worker_for(client: Client, *workflow_classes) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=list(workflow_classes),
        activities=ALL_ACTIVITIES,
        activity_executor=ThreadPoolExecutor(5),
    )


async def wait_for_status(handle: WorkflowHandle, status: str) -> None:
    for _ in range(100):
        if (await handle.query("get_status")).startswith(status):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached {status}")


async def capture(
    env, module, order: str = "hist", workflow_type: str = "OrderWorkflow", completed: bool = False
):
    """History of an order started under `module`'s workflow(s).

    By default the order is parked awaiting acknowledgement. Commands the new code adds *after* the
    last recorded workflow task are not checked, so for changes placed after that point use
    `completed=True` (acknowledged and finished) to get a history that covers them.
    """
    async with worker_for(env.client, getattr(module, workflow_type)):
        handle = await env.client.start_workflow(
            workflow_type, order, id=f"order-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        await wait_for_status(handle, "SHIPPED")
        if completed:
            await handle.signal("acknowledge_order")
            await handle.result()
        return await handle.fetch_history()


async def replay(workflow_cls, history) -> WorkflowReplayResult:
    classes = workflow_cls if isinstance(workflow_cls, (list, tuple)) else [workflow_cls]
    return await Replayer(workflows=list(classes)).replay_workflow(
        history, raise_on_replay_failure=False
    )


async def replays_cleanly(workflow_cls, history) -> bool:
    return (await replay(workflow_cls, history)).replay_failure is None
