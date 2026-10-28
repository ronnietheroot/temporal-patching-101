"""Simulated marketplace activities.

State lives in this worker process only (a dict), which is plenty for a demo. Set
ACTIVITY_DELAY_MIN / ACTIVITY_DELAY_MAX (seconds, default 1 / 4) bound a random per-call sleep that
simulates real API latency and widens the window for sending signals/updates.
Special order ids: "*oos*" -> out of stock, "*shipfail*" -> shipping fails.
"""

import os
import random
import time

from temporalio import activity
from temporalio.exceptions import ApplicationError

from models import Order, OrderItem, ShipmentInfo

INVENTORY: dict[str, dict[str, int]] = {
    "east": {"WIDGET-1": 50, "GADGET-2": 10},
    "west": {"WIDGET-1": 5, "GADGET-2": 40},
}


def _delay() -> None:
    low = float(os.environ.get("ACTIVITY_DELAY_MIN", "1"))
    high = float(os.environ.get("ACTIVITY_DELAY_MAX", "4"))
    time.sleep(random.uniform(low, max(low, high)))


@activity.defn
def get_order(order_id: str) -> Order:
    _delay()
    sku = "GADGET-99999" if "oos" in order_id else "WIDGET-1"
    return Order(
        order_id=order_id,
        customer_id="cust-42",
        shipping_address="1 Market St, San Francisco, CA",
        items=[OrderItem(sku=sku, quantity=2)],
    )


@activity.defn
def check_stock(order: Order) -> bool:
    """Is the catalog item sellable at all (any warehouse has enough units)?"""
    _delay()
    for item in order.items:
        total = sum(w.get(item.sku, 0) for w in INVENTORY.values())
        if total < item.quantity:
            raise ApplicationError(
                f"{item.sku} is out of stock", type="OutOfStock", non_retryable=True
            )
    return True


@activity.defn
def check_inventory(order: Order) -> str:
    """Pick a single warehouse that can fulfil the whole order."""
    _delay()
    for name, stock in INVENTORY.items():
        if all(stock.get(i.sku, 0) >= i.quantity for i in order.items):
            return name
    raise ApplicationError("No single warehouse can fulfil order", non_retryable=True)


@activity.defn
def check_fraud(order: Order) -> bool:
    """Added in v2 of the workflow (the change we patch in the demo)."""
    _delay()
    return "fraud" not in order.order_id


@activity.defn
def update_inventory(order: Order, warehouse: str) -> None:
    _delay()
    for item in order.items:
        INVENTORY[warehouse][item.sku] -= item.quantity


@activity.defn
def restore_inventory(order: Order, warehouse: str) -> None:
    """Saga compensation for update_inventory."""
    for item in order.items:
        INVENTORY[warehouse][item.sku] += item.quantity


@activity.defn
def ship_order(order: Order, warehouse: str, address: str) -> ShipmentInfo:
    _delay()
    if "shipfail" in order.order_id:
        raise ApplicationError("Carrier rejected shipment", non_retryable=True)
    return ShipmentInfo(
        tracking_number=f"TRK-{order.order_id}", address=address, warehouse=warehouse
    )


@activity.defn
def send_shipment_notification(order: Order, shipment: ShipmentInfo) -> None:
    """Added by the `notify-on-ship` patch."""
    _delay()


@activity.defn
def award_loyalty_points(order: Order) -> int:
    """Added by the `award-loyalty-points` patch."""
    _delay()
    return sum(item.quantity for item in order.items) * 10
