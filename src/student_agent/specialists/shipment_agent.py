from __future__ import annotations

from datetime import datetime
from typing import Any

from ._collector import EvidenceCollector


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("shipment timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("shipment timestamp must include a timezone")
    return parsed


async def collect_shipment(order_id: str, collector: EvidenceCollector) -> dict[str, Any]:
    summary = await collector.call(
        "get_shipment_summary", actor="shipment-agent", domain="shipment", order_id=order_id
    )
    if not isinstance(summary, dict) or summary.get("order_id") != order_id:
        raise ValueError("get_shipment_summary returned a different or invalid order")
    shipping_limits = summary.get("shipping_limits")
    if not isinstance(shipping_limits, list) or not all(
        isinstance(row, dict) for row in shipping_limits
    ):
        raise ValueError("get_shipment_summary returned invalid shipping limits")
    carrier_at = _timestamp(summary.get("delivered_carrier_at"))
    customer_at = _timestamp(summary.get("delivered_customer_at"))
    estimate_at = _timestamp(summary.get("estimated_delivery_at"))
    late_sellers: list[str] = []
    if carrier_at:
        for row in shipping_limits:
            limit_at = _timestamp(row.get("shipping_limit_at"))
            seller_id = row.get("seller_id")
            if limit_at and carrier_at > limit_at and seller_id and seller_id not in late_sellers:
                late_sellers.append(seller_id)
    return {
        "summary": summary,
        "late_handoff_seller_ids": late_sellers,
        "delivered_late": customer_at > estimate_at if customer_at and estimate_at else None,
    }
