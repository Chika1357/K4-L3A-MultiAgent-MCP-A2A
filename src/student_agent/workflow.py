from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from .mcp_gateway import EvidenceGateway
from .policy import PolicyAgent
from .specialists import collect_case_evidence
from .trace import TraceWriter
from .verifier import VerifierAgent


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _in_window(value: str | None, start: datetime, end: datetime) -> bool:
    timestamp = _time(value)
    return timestamp is not None and start <= timestamp <= end


def _money(value: Any) -> Decimal:
    return Decimal(str(value))


def _align_responsible_parties(
    parties: list[dict[str, Any]], items: list[dict[str, Any]], late_sellers: list[str]
) -> list[dict[str, Any]]:
    seller_ids = list(dict.fromkeys(row["seller_id"] for row in items if row.get("seller_id")))
    aligned = []
    for party in parties:
        if party.get("party_type") != "seller":
            aligned.append(party)
            continue
        seller_id = party.get("party_id")
        if late_sellers:
            seller_id = late_sellers[0]
        elif seller_id not in seller_ids:
            seller_id = seller_ids[0] if len(seller_ids) == 1 else None
        aligned.append({"party_type": "seller", "party_id": seller_id})
    return aligned


def _relevant_tools(issue: str) -> tuple[str, ...]:
    common = ("get_order", "get_policy")
    if issue in {"canceled_order_paid", "unavailable_order_paid"}:
        return (*common, "get_order_items", "get_payment_timeline")
    if issue in {"late_delivery_seller", "late_delivery_logistics"}:
        return (*common, "get_order_items", "get_shipment_summary")
    if issue in {"valid_split_payment", "payment_mismatch", "duplicate_charge"}:
        return (*common, "get_order_items", "get_order_payments", "get_payment_timeline")
    if issue in {"refund_pending", "refund_failed"}:
        return (*common, "get_payment_timeline", "get_refund_timeline")
    return (*common, "get_shipment_summary")


def _assess_claims(
    case: dict[str, Any], issue: str, refund: Decimal, refs: list[str]
) -> list[dict[str, Any]]:
    claims = case.get("customer_request", {}).get("claims", [])
    assessments = []
    for claim in claims[:5]:
        topic = claim.get("topic")
        if issue == "insufficient_evidence":
            verdict = "insufficient_evidence"
        elif topic == issue:
            verdict = "supported"
        elif topic == "requested_full_refund":
            if issue in {"canceled_order_paid", "unavailable_order_paid"} and refund > 0:
                verdict = "supported"
            elif refund > 0:
                verdict = "partially_supported"
            else:
                verdict = "unsupported"
        else:
            verdict = "unsupported"
        assessments.append(
            {
                "claim_id": claim["claim_id"],
                "verdict": verdict,
                "confidence": 0.5 if verdict == "insufficient_evidence" else 0.85,
                "evidence_refs": refs,
            }
        )
    return assessments


def _analyze(case: dict[str, Any], collected: dict[str, Any]) -> dict[str, Any]:
    order = collected["order"]["order"]
    payment = collected["payment"]
    shipment = collected["shipment"]["summary"]
    purchased = _time(order.get("order_purchase_timestamp"))
    opened = _time(case["opened_at"])
    if purchased is None or opened is None or purchased > opened:
        raise ValueError(f"{case['case_id']}: invalid order or case timeline")

    items = [
        row
        for row in collected["order"]["items"]
        if _in_window(row.get("shipping_limit_date"), purchased, opened)
    ]
    items = list({row["order_item_id"]: row for row in items}.values())
    expected_total = sum(
        (_money(row.get("price", 0)) + _money(row.get("freight_value", 0)) for row in items),
        Decimal("0"),
    )
    payment_events = [
        row
        for row in payment["payment_events"]
        if _in_window(row.get("event_at"), purchased, opened)
    ]
    captures = [
        row
        for row in payment_events
        if row.get("event_type") == "captured" and row.get("status") == "confirmed"
    ]
    paid_total = sum((_money(row["amount_brl"]) for row in captures), Decimal("0"))
    refunds = [
        row
        for row in (payment["refund_events"] or [])
        if _in_window(row.get("event_at"), purchased, opened)
    ]
    latest_refund = max(refunds, key=lambda row: row["event_at"], default=None)

    carrier_at = _time(shipment.get("delivered_carrier_at"))
    delivered_at = _time(shipment.get("delivered_customer_at"))
    estimated_at = _time(shipment.get("estimated_delivery_at"))
    delivered_late = bool(
        estimated_at
        and estimated_at < opened
        and (delivered_at is None or delivered_at > estimated_at)
    )
    late_sellers = list(
        dict.fromkeys(
            row["seller_id"]
            for row in items
            if row.get("seller_id")
            and carrier_at
            and carrier_at <= opened
            and carrier_at > _time(row.get("shipping_limit_date"))
        )
    )

    topic = case["customer_request"]["claims"][0]["topic"]
    status = order.get("order_status")
    refund_status = latest_refund.get("status") if latest_refund else None
    has_mismatch_event = any(
        row.get("event_type") == "reconciliation_mismatch" for row in payment_events
    )
    if status == "canceled" and paid_total > 0:
        issue = "canceled_order_paid"
    elif status == "unavailable" and paid_total > 0:
        issue = "unavailable_order_paid"
    elif refund_status == "failed":
        issue = "refund_failed"
    elif refund_status == "pending":
        issue = "refund_pending"
    elif delivered_late:
        issue = "late_delivery_seller" if late_sellers else "late_delivery_logistics"
    elif topic == "duplicate_charge" and len(captures) >= 2 and paid_total > expected_total:
        issue = "duplicate_charge"
    elif has_mismatch_event:
        issue = "payment_mismatch"
    elif len(captures) >= 2 and expected_total > 0 and paid_total == expected_total:
        issue = "valid_split_payment"
    elif expected_total > 0 and paid_total != expected_total and topic == "payment_mismatch":
        issue = "payment_mismatch"
    elif status == "delivered" and delivered_at and delivered_at <= opened:
        issue = "unsupported_claim"
    else:
        issue = "insufficient_evidence"

    captured_amounts = Counter(_money(row["amount_brl"]) for row in captures)
    active_payments = []
    for row in payment["payments"]:
        amount = _money(row.get("payment_value", 0))
        if captured_amounts[amount] > 0:
            active_payments.append(row)
            captured_amounts[amount] -= 1

    return {
        "issue": issue,
        "status": status,
        "paid_total": paid_total,
        "expected_total": expected_total,
        "items": items,
        "active_payments": active_payments,
        "late_sellers": late_sellers,
        "delivered_late": delivered_late,
        "latest_refund": latest_refund,
    }


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    case_id = case["case_id"]
    trace.emit(
        case_id=case_id, event_type="task_assigned", actor="coordinator", target="specialists"
    )
    collected = await collect_case_evidence(case, gateway, trace)
    analysis = _analyze(case, collected)
    issue = analysis["issue"]
    envelopes = collected["evidence_by_tool"]
    refs = [envelopes[tool]["evidence_ref"] for tool in _relevant_tools(issue) if tool in envelopes]
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="coordinator",
        target="policy-agent",
        evidence_refs=refs,
    )

    policy_facts = {
        "order_status": analysis["status"],
        "order_total": float(
            analysis["expected_total"] if issue == "payment_mismatch" else analysis["paid_total"]
        ),
        "seller_id": analysis["late_sellers"][0] if analysis["late_sellers"] else None,
        "payment_info": {
            "total_paid": float(analysis["paid_total"]),
            "is_duplicate": issue == "duplicate_charge",
            "is_mismatch": issue == "payment_mismatch",
            "is_split": issue == "valid_split_payment",
            "is_valid_split": issue == "valid_split_payment",
        },
        "shipment_info": {
            "is_late": issue in {"late_delivery_seller", "late_delivery_logistics"},
            "late_by_seller": issue == "late_delivery_seller",
        },
        "refund_info": {
            "status": analysis["latest_refund"].get("status")
            if issue in {"refund_pending", "refund_failed"} and analysis["latest_refund"]
            else None,
            "amount_brl": analysis["latest_refund"].get("amount_brl")
            if analysis["latest_refund"]
            else None,
        },
        "evidence_refs": refs,
    }
    decision = PolicyAgent().evaluate(case, policy_facts)
    rules = (collected["policy"] or {}).get("rules", {})
    rule = rules.get(issue)
    if not isinstance(rule, dict):
        issue = "insufficient_evidence"
        rule = {}
    refund = _money(rule.get("refund_brl", 0))
    decision.update(
        {
            "primary_issue": issue,
            "case_status": rule.get("case_status", "needs_investigation"),
            "responsible_parties": _align_responsible_parties(
                rule.get("responsible_parties", [{"party_type": "unknown", "party_id": None}]),
                analysis["items"],
                analysis["late_sellers"],
            ),
            "recommended_refund_brl": float(refund),
            "refund_lines": (
                [
                    {
                        "reason_code": rule["recommended_action"],
                        "amount_brl": float(refund),
                        "entity_id": collected["order_id"],
                    }
                ]
                if refund > 0
                else []
            ),
            "resolution_actions": [rule.get("recommended_action", "investigate_evidence")],
            "data_conflicts": [],
            "claim_assessments": _assess_claims(case, issue, refund, refs),
        }
    )
    trace.emit(
        case_id=case_id, event_type="policy_decided", actor="policy-agent", decision_code=issue
    )
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy-agent",
        target="verifier",
        evidence_refs=refs,
    )

    entities = {
        "order_ids": [collected["order_id"]],
        "item_ids": [row["order_item_id"] for row in analysis["items"] if row.get("order_item_id")],
        "seller_ids": [row["seller_id"] for row in analysis["items"] if row.get("seller_id")],
        "payment_references": [
            row["payment_reference"]
            for row in analysis["active_payments"]
            if row.get("payment_reference")
        ],
        "shipment_ids": [shipment_id]
        if (shipment_id := collected["shipment"]["summary"].get("shipment_id"))
        else [],
    }
    output = VerifierAgent(trace.contracts).verify_and_build(case_id, decision, entities, refs)
    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="PASS",
    )
    return output
