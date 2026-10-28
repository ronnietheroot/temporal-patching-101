import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from activities import (
    audit_item,
    award_loyalty_points,
    check_fraud,
    check_inventory,
    check_stock,
    get_order,
    hold_inventory,
    reserve_inventory,
    restore_inventory,
    send_shipment_notification,
    ship_order,
    update_inventory,
)
from models import TASK_QUEUE
from workflows import OrderWorkflow
from workflows_v2 import OrderWorkflowV2


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    connect_config = ClientConfig.load_client_connect_config()
    connect_config.setdefault("target_host", "localhost:7233")
    client = await Client.connect(**connect_config)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[OrderWorkflow, OrderWorkflowV2],
            activities=[
                get_order,
                check_stock,
                check_inventory,
                check_fraud,
                update_inventory,
                restore_inventory,
                ship_order,
                send_shipment_notification,
                award_loyalty_points,
                reserve_inventory,
                hold_inventory,
                audit_item,
            ],
            activity_executor=executor,
        )
        print(f"Worker listening on task queue '{TASK_QUEUE}'")
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
