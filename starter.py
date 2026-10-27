"""CLI client for the demo. Uses string workflow/handler names so it works with any scenario."""

import argparse
import asyncio

from temporalio.client import Client
from temporalio.envconfig import ClientConfig

from models import TASK_QUEUE


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "ack", "cancel", "status", "result"):
        sub.add_parser(name).add_argument("order_id")
    addr = sub.add_parser("address")
    addr.add_argument("order_id")
    addr.add_argument("new_address")
    args = parser.parse_args()

    connect_config = ClientConfig.load_client_connect_config()
    connect_config.setdefault("target_host", "localhost:7233")
    client = await Client.connect(**connect_config)
    workflow_id = f"order-{args.order_id}"

    if args.cmd == "start":
        handle = await client.start_workflow(
            "OrderWorkflow", args.order_id, id=workflow_id, task_queue=TASK_QUEUE
        )
        print(f"Started {handle.id} (run {handle.result_run_id})")
        return

    handle = client.get_workflow_handle(workflow_id)
    if args.cmd == "ack":
        await handle.signal("acknowledge_order")
        print(f"Signalled acknowledge_order to {workflow_id}")
    elif args.cmd == "cancel":
        await handle.signal("cancel_order")
        print(f"Signalled cancel_order to {workflow_id}")
    elif args.cmd == "address":
        print(
            "Updated address:",
            await handle.execute_update("change_shipping_address", args.new_address),
        )
    elif args.cmd == "status":
        print(await handle.query("get_status"))
    elif args.cmd == "result":
        print(await handle.result())


if __name__ == "__main__":
    asyncio.run(main())
