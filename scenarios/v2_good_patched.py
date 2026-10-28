"""v2 (PROPER, step 1): same change guarded by workflow.patched().

Old histories have no marker -> patched() is False -> they skip the fraud check and replay
cleanly. New executions record the marker and run the fraud check.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from activities import (
        check_fraud,
        check_inventory,
        check_stock,
        get_order,
        restore_inventory,
        ship_order,
        update_inventory,
    )
    from models import OrderStatus, ShipmentInfo

ACTIVITY_OPTS = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self._status = OrderStatus.CREATED
        self._address = ""
        self._acknowledged = False
        self._cancelled = False

    @workflow.run
    async def run(self, order_id: str) -> str:
        order = await workflow.execute_activity(get_order, order_id, **ACTIVITY_OPTS)
        self._address = order.shipping_address
        self._status = OrderStatus.CHECKING

        await workflow.execute_activity(check_stock, order, **ACTIVITY_OPTS)
        warehouse = await workflow.execute_activity(check_inventory, order, **ACTIVITY_OPTS)

        if workflow.patched("order-fraud-check"):  # noqa: SIM102 (kept nested for readability)
            if not await workflow.execute_activity(check_fraud, order, **ACTIVITY_OPTS):
                self._status = OrderStatus.FAILED
                raise ApplicationError("Order flagged as fraudulent", non_retryable=True)

        await workflow.execute_activity(update_inventory, args=[order, warehouse], **ACTIVITY_OPTS)
        self._status = OrderStatus.RESERVED

        try:
            shipment: ShipmentInfo = await workflow.execute_activity(
                ship_order, args=[order, warehouse, self._address], **ACTIVITY_OPTS
            )
        except ActivityError:
            # Saga compensation: put the stock back.
            await workflow.execute_activity(
                restore_inventory, args=[order, warehouse], **ACTIVITY_OPTS
            )
            self._status = OrderStatus.FAILED
            raise
        self._status = OrderStatus.SHIPPED

        await workflow.wait_condition(lambda: self._acknowledged or self._cancelled)

        if self._cancelled:
            self._status = OrderStatus.CANCELLED
            return f"{shipment.tracking_number}: cancelled"
        self._status = OrderStatus.DELIVERED
        return f"{shipment.tracking_number}: delivered and acknowledged"

    @workflow.signal
    def acknowledge_order(self) -> None:
        """Customer confirms they received the order."""
        self._acknowledged = True

    @workflow.signal
    def cancel_order(self) -> None:
        self._cancelled = True

    @workflow.update
    def change_shipping_address(self, new_address: str) -> str:
        self._address = new_address
        return self._address

    @change_shipping_address.validator
    def _validate_address(self, new_address: str) -> None:
        if self._status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.CANCELLED):
            raise ValueError(f"Cannot change address: order is {self._status}")
        if not new_address.strip():
            raise ValueError("Address must not be empty")

    @workflow.query
    def get_status(self) -> str:
        return f"{self._status} (address: {self._address})"
