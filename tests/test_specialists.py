from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from student_agent.contracts import Contracts
from student_agent.mcp_gateway import EvidenceGateway
from student_agent.specialists import collect_case_evidence
from student_agent.trace import TraceWriter


class GatewayStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.errors: dict[str, RuntimeError] = {}

    async def list_tools(self) -> list[str]:
        return list(self.responses)

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append((tool_name, case_id, arguments))
        if tool_name in self.errors:
            raise self.errors[tool_name]
        return self.responses[tool_name]

    responses = {
        "get_order": {
            "domain": "order",
            "evidence_ref": "ev_" + "A" * 24,
            "data": {"order_id": "ORDER_1", "order_status": "delivered"},
        },
        "get_order_items": {
            "domain": "item",
            "evidence_ref": "ev_" + "B" * 24,
            "data": [{"order_id": "ORDER_1", "order_item_id": "ITEM_1", "seller_id": "SELLER_1"}],
        },
        "get_order_payments": {
            "domain": "payment",
            "evidence_ref": "ev_" + "C" * 24,
            "data": [
                {"order_id": "ORDER_1", "payment_type": "card", "payment_value": "10.25"},
                {"order_id": "ORDER_1", "payment_type": "voucher", "payment_value": "4.75"},
            ],
        },
        "get_payment_timeline": {
            "domain": "payment",
            "evidence_ref": "ev_" + "D" * 24,
            "data": {"order_id": "ORDER_1", "events": []},
        },
        "get_refund_timeline": {
            "domain": "refund",
            "evidence_ref": "ev_" + "E" * 24,
            "data": {"order_id": "ORDER_1", "events": []},
        },
        "get_shipment_summary": {
            "domain": "shipment",
            "evidence_ref": "ev_" + "F" * 24,
            "data": {
                "order_id": "ORDER_1",
                "delivered_carrier_at": "2018-01-03T00:00:00-03:00",
                "delivered_customer_at": "2018-01-08T00:00:00-03:00",
                "estimated_delivery_at": "2018-01-07T00:00:00-03:00",
                "shipping_limits": [
                    {"seller_id": "SELLER_1", "shipping_limit_at": "2018-01-02T00:00:00-03:00"}
                ],
            },
        },
        "get_policy": {
            "domain": "policy",
            "evidence_ref": "ev_" + "G" * 24,
            "data": {"policy_version": "V1", "rules": {}},
        },
    }


def test_specialists_scope_calls_and_trace(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, Contracts(root / "contracts" / "schemas"))
    gateway = GatewayStub()
    case = {
        "case_id": "CASE_001",
        "customer_request": {"claimed_order_id": "ORDER_1"},
        "policy_version": "V1",
    }

    result = asyncio.run(collect_case_evidence(case, gateway, trace))

    assert all(case_id == "CASE_001" for _, case_id, _ in gateway.calls)
    assert all(
        args == ({"policy_version": "V1"} if tool == "get_policy" else {"order_id": "ORDER_1"})
        for tool, _, args in gateway.calls
    )
    assert result["payment"]["paid_total_brl"] == "15.00"
    assert result["shipment"]["late_handoff_seller_ids"] == ["SELLER_1"]
    assert result["shipment"]["delivered_late"] is True
    assert result["evidence_refs"] == [
        gateway.responses[name]["evidence_ref"] for name, _, _ in gateway.calls
    ]

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == len(gateway.calls)
    assert all(event["event_type"] == "tool_result_consumed" for event in events)
    assert [event["tool_name"] for event in events] == [name for name, _, _ in gateway.calls]
    assert [event["evidence_refs"][0] for event in events] == result["evidence_refs"]


def test_specialists_reject_cross_order_data(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    trace = TraceWriter(tmp_path / "trace.jsonl", Contracts(root / "contracts" / "schemas"))
    gateway = GatewayStub()
    gateway.responses = {
        **gateway.responses,
        "get_order": {
            **gateway.responses["get_order"],
            "data": {"order_id": "OTHER_ORDER"},
        },
    }
    case = {"case_id": "CASE_001", "customer_request": {"claimed_order_id": "ORDER_1"}}
    with pytest.raises(ValueError, match="different or invalid order"):
        asyncio.run(collect_case_evidence(case, gateway, trace))


def test_missing_refund_timeline_is_explicit(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, Contracts(root / "contracts" / "schemas"))
    gateway = GatewayStub()
    gateway.errors["get_refund_timeline"] = RuntimeError(
        "MCP tool get_refund_timeline failed: Error executing tool get_refund_timeline"
    )
    case = {
        "case_id": "CASE_001",
        "customer_request": {"claimed_order_id": "ORDER_1"},
        "policy_version": "V1",
    }

    result = asyncio.run(collect_case_evidence(case, gateway, trace))

    assert result["payment"]["refund_timeline"] is None
    assert result["payment"]["refund_events"] is None
    assert result["payment"]["unavailable_tools"] == ["get_refund_timeline"]
    assert "get_refund_timeline" not in result["evidence_by_tool"]
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 6
    assert all(event["tool_name"] != "get_refund_timeline" for event in events)


def test_gateway_reads_current_mcp_result_fields() -> None:
    class SessionStub:
        async def call_tool(self, name: str, *, arguments: dict[str, str]) -> Any:
            assert name == "get_order"
            assert arguments == {"case_id": "CASE_001", "order_id": "ORDER_1"}
            return SimpleNamespace(
                is_error=False,
                structured_content={
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_" + "A" * 24,
                    "result_hash": "sha256:" + "a" * 64,
                    "domain": "order",
                    "data": {"order_id": "ORDER_1"},
                },
                content=[],
            )

    root = Path(__file__).resolve().parents[1]
    gateway = EvidenceGateway(SessionStub(), Contracts(root / "contracts" / "schemas"))
    result = asyncio.run(gateway.call("get_order", case_id="CASE_001", order_id="ORDER_1"))
    assert result["data"]["order_id"] == "ORDER_1"
