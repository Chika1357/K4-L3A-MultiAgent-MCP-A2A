from pathlib import Path
import pytest

from student_agent.contracts import Contracts
from student_agent.policy import PolicyAgent
from student_agent.verifier import VerifierAgent


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


def test_canceled_order_paid(contracts: Contracts) -> None:
    policy = PolicyAgent()
    verifier = VerifierAgent(contracts)

    case = {"case_id": "L3A_CASE_001", "customer_message": "Eu paguei mas cancelaram meu pedido"}
    facts = {
        "order_status": "canceled",
        "payment_info": {"total_paid": 120.50},
        "seller_id": "seller_abc",
    }

    result = policy.evaluate(case, facts)
    assert result["primary_issue"] == "canceled_order_paid"
    assert result["case_status"] == "action_required"
    assert result["recommended_refund_brl"] == 120.50

    output = verifier.verify_and_build(
        case_id="L3A_CASE_001",
        policy_result=result,
        affected_entities={"order_ids": ["ord_123"], "item_ids": [], "seller_ids": [], "payment_references": [], "shipment_ids": []},
        evidence_refs=["ev_123456789012345678901234"],
    )

    assert output["schema_version"] == "day09-l3a-output-v2"
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["financial_resolution"]["recommended_refund_brl"] == 120.50
    assert output["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "CANCELED_ORDER_PAID"


def test_unsupported_claim_no_refund(contracts: Contracts) -> None:
    policy = PolicyAgent()
    verifier = VerifierAgent(contracts)

    case = {"case_id": "L3A_CASE_002", "customer_message": "Minha entrega atrasou demais"}
    facts = {
        "order_status": "delivered",
        "shipment_info": {"is_late": False},
        "payment_info": {"total_paid": 89.00},
    }

    result = policy.evaluate(case, facts)
    assert result["primary_issue"] == "unsupported_claim"
    assert result["case_status"] == "no_action"
    assert result["recommended_refund_brl"] == 0.0

    output = verifier.verify_and_build(
        case_id="L3A_CASE_002",
        policy_result=result,
        affected_entities={"order_ids": ["ord_456"], "item_ids": [], "seller_ids": [], "payment_references": [], "shipment_ids": []},
        evidence_refs=["ev_987654321098765432109876"],
    )

    assert output["assessment"]["primary_issue"] == "unsupported_claim"
    assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
    assert output["financial_resolution"]["refund_lines"] == []


def test_late_delivery_seller(contracts: Contracts) -> None:
    policy = PolicyAgent()
    verifier = VerifierAgent(contracts)

    case = {"case_id": "L3A_CASE_003", "customer_message": "Vendedor demorou para enviar"}
    facts = {
        "order_status": "delivered",
        "shipment_info": {
            "is_late": True,
            "late_by_seller": True,
            "delay_compensation_brl": 15.00,
        },
        "seller_id": "seller_vip_1",
    }

    result = policy.evaluate(case, facts)
    assert result["primary_issue"] == "late_delivery_seller"
    assert any(p["party_type"] == "seller" for p in result["responsible_parties"])

    output = verifier.verify_and_build(
        case_id="L3A_CASE_003",
        policy_result=result,
        affected_entities={"order_ids": ["ord_789"], "item_ids": [], "seller_ids": ["seller_vip_1"], "payment_references": [], "shipment_ids": []},
        evidence_refs=["ev_abcdefghij12345678901234"],
    )

    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
    assert output["financial_resolution"]["recommended_refund_brl"] == 15.00


def test_verifier_rejects_seller_outside_order(contracts: Contracts) -> None:
    result = PolicyAgent().evaluate(
        {"case_id": "L3A_CASE_003"},
        {
            "order_status": "delivered",
            "shipment_info": {"is_late": True, "late_by_seller": True},
            "seller_id": "seller-from-policy",
        },
    )

    output = VerifierAgent(contracts).verify_and_build(
        case_id="L3A_CASE_003",
        policy_result=result,
        affected_entities={"order_ids": [], "item_ids": [], "seller_ids": ["seller-from-order"], "payment_references": [], "shipment_ids": []},
        evidence_refs=["ev_abcdefghij12345678901234"],
    )

    assert output["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": None}
    ]


def test_real_input_structure_with_claims(contracts: Contracts) -> None:
    policy = PolicyAgent()
    verifier = VerifierAgent(contracts)

    case = {
        "case_id": "L3A_CASE_001",
        "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Đơn hàng có dấu hiệu bất thường sau thanh toán.",
            "claimed_order_id": "e2a03ccf5ea816036608b2d8c3ab8e60",
            "claims": [
                {"claim_id": "claim-001-a", "topic": "canceled_order_paid"},
                {"claim_id": "claim-001-b", "topic": "requested_full_refund"}
            ]
        },
        "policy_version": "EC_POLICY_V1"
    }

    facts = {
        "order_status": "canceled",
        "payment_info": {"total_paid": 250.00},
        "seller_id": "seller_001",
        "evidence_refs": ["ev_111122223333444455556666"],
    }

    result = policy.evaluate(case, facts)
    output = verifier.verify_and_build(
        case_id="L3A_CASE_001",
        policy_result=result,
        affected_entities={
            "order_ids": ["e2a03ccf5ea816036608b2d8c3ab8e60"],
            "item_ids": [],
            "seller_ids": ["seller_001"],
            "payment_references": [],
            "shipment_ids": []
        },
        evidence_refs=["ev_111122223333444455556666"],
    )

    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert len(output["claim_assessments"]) == 2
    assert output["claim_assessments"][0]["verdict"] == "supported"
    assert output["claim_assessments"][1]["verdict"] == "supported"
    assert output["claim_assessments"][0]["evidence_refs"] == ["ev_111122223333444455556666"]

