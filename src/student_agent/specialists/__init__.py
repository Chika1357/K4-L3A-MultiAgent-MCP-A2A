from __future__ import annotations

from collections.abc import Collection
from typing import Any

from student_agent.mcp_gateway import EvidenceGateway
from student_agent.trace import TraceWriter

from ._collector import EvidenceCollector
from .order_agent import collect_order
from .payment_agent import collect_payment
from .shipment_agent import collect_shipment


async def collect_case_evidence(
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
    *,
    available_tools: Collection[str] | None = None,
) -> dict[str, Any]:
    """Return case-scoped facts and original MCP envelopes for policy and verification.

    A coordinator should emit case_received/task_assigned before calling this. Policy
    must select only relevant refs from evidence_by_tool for the final answer.
    """
    case_id = case["case_id"]
    order_id = case["customer_request"]["claimed_order_id"]
    if not isinstance(case_id, str) or not isinstance(order_id, str) or not order_id:
        raise ValueError("case_id and claimed_order_id must be non-empty strings")

    tools = set(available_tools) if available_tools is not None else set(await gateway.list_tools())
    collector = EvidenceCollector(case_id, gateway, trace, tools)
    order = await collect_order(order_id, collector)
    payment = await collect_payment(order_id, collector)
    shipment = await collect_shipment(order_id, collector)

    policy = None
    policy_version = case.get("policy_version")
    if policy_version and "get_policy" in tools:
        policy = await collector.call(
            "get_policy", actor="policy-agent", domain="policy", policy_version=policy_version
        )
        if not isinstance(policy, dict) or policy.get("policy_version") != policy_version:
            raise ValueError("get_policy returned a different or invalid version")

    return {
        "case_id": case_id,
        "order_id": order_id,
        "order": order,
        "payment": payment,
        "shipment": shipment,
        "policy": policy,
        "evidence_refs": collector.refs(),
        "evidence_by_tool": collector.evidence_by_tool,
    }


__all__ = ["collect_case_evidence", "collect_order", "collect_payment", "collect_shipment"]
