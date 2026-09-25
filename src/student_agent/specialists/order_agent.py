from __future__ import annotations

from typing import Any

from ._collector import EvidenceCollector


def _rows(value: Any, tool_name: str, order_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"{tool_name} returned invalid rows")
    if any(row.get("order_id") != order_id for row in value):
        raise ValueError(f"{tool_name} returned rows for a different order")
    return value


async def collect_order(order_id: str, collector: EvidenceCollector) -> dict[str, Any]:
    order = await collector.call(
        "get_order", actor="order-agent", domain="order", order_id=order_id
    )
    if not isinstance(order, dict) or order.get("order_id") != order_id:
        raise ValueError("get_order returned a different or invalid order")

    items = _rows(
        await collector.call(
            "get_order_items", actor="order-agent", domain="item", order_id=order_id
        ),
        "get_order_items",
        order_id,
    )
    return {
        "order": order,
        "items": items,
        "item_ids": list(
            dict.fromkeys(row["order_item_id"] for row in items if row.get("order_item_id"))
        ),
        "seller_ids": list(
            dict.fromkeys(row["seller_id"] for row in items if row.get("seller_id"))
        ),
    }
