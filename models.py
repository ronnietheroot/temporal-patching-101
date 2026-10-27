from dataclasses import dataclass, field

TASK_QUEUE = "orders"


@dataclass
class OrderItem:
    sku: str
    quantity: int


@dataclass
class Order:
    order_id: str
    customer_id: str
    shipping_address: str
    items: list[OrderItem] = field(default_factory=list)


@dataclass
class ShipmentInfo:
    tracking_number: str
    address: str
    warehouse: str


class OrderStatus:
    CREATED = "CREATED"
    CHECKING = "CHECKING"
    RESERVED = "RESERVED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ACK_TIMEOUT = "ACK_TIMEOUT"
