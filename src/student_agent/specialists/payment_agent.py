from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ._collector import EvidenceCollector
from .order_agent import _rows


async def collect_payment(order_id: str, collector: EvidenceCollector) -> dict[str, Any]:
    payments = _rows(
        await collector.call(
            "get_order_payments", actor="payment-agent", domain="payment", order_id=order_id
        ),
        "get_order_payments",
        order_id,
    )
    timeline = await collector.call(
        "get_payment_timeline", actor="payment-agent", domain="payment", order_id=order_id
    )
    refund = None
    unavailable_tools: list[str] = []
    try:
        refund = await collector.call(
            "get_refund_timeline", actor="payment-agent", domain="refund", order_id=order_id
        )
    except RuntimeError as exc:
        expected_error = (
            "MCP tool get_refund_timeline failed: Error executing tool get_refund_timeline"
        )
        if str(exc) != expected_error:
            raise
        unavailable_tools.append("get_refund_timeline")
    if not isinstance(timeline, dict) or timeline.get("order_id") != order_id:
        raise ValueError("get_payment_timeline returned a different or invalid order")
    if refund is not None and (not isinstance(refund, dict) or refund.get("order_id") != order_id):
        raise ValueError("get_refund_timeline returned a different or invalid order")
    payment_events = _rows(timeline.get("events"), "get_payment_timeline", order_id)
    refund_events = (
        _rows(refund.get("events"), "get_refund_timeline", order_id) if refund is not None else None
    )

    try:
        paid_total = sum((Decimal(str(row["payment_value"])) for row in payments), Decimal("0"))
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("get_order_payments returned an invalid payment value") from exc
    return {
        "payments": payments,
        "payment_timeline": timeline,
        "refund_timeline": refund,
        "payment_events": payment_events,
        "refund_events": refund_events,
        "unavailable_tools": unavailable_tools,
        "paid_total_brl": str(paid_total),
        "payment_types": list(
            dict.fromkeys(row["payment_type"] for row in payments if row.get("payment_type"))
        ),
    }
