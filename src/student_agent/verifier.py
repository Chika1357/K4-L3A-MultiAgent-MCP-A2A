from __future__ import annotations

import re
from typing import Any

from .contracts import Contracts

EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")
CAUSE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")


class VerifierAgent:
    """Agent chốt chặn an toàn: Kiểm tra tính nhất quán logic, hiệu chuẩn confidence và validate schema."""

    def __init__(self, contracts: Contracts) -> None:
        self.contracts = contracts

    def verify_and_build(
        self,
        case_id: str,
        policy_result: dict[str, Any],
        affected_entities: dict[str, list[str]],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        """
        Kiểm tra toàn bộ ràng buộc nghiệp vụ, đóng gói output và xác thực qua schema.

        Args:
            case_id: ID của case đang xét (e.g., 'L3A_CASE_001')
            policy_result: Kết quả đánh giá từ PolicyAgent
            affected_entities: Danh sách các entity ID (order_ids, item_ids, ...)
            evidence_refs: Danh sách các ref nhận được từ MCP Gateway

        Returns:
            Dictionary output hoàn chỉnh, khớp 100% với contracts/schemas/l3a-output-v2.schema.json
        """
        primary_issue = policy_result["primary_issue"]
        case_status = policy_result["case_status"]
        recommended_refund = float(policy_result.get("recommended_refund_brl", 0.0))
        refund_lines = list(policy_result.get("refund_lines", []))
        data_conflicts = list(policy_result.get("data_conflicts", []))

        # --- 1. LỌC VÀ CHUẨN HÓA EVIDENCE REFS ---
        valid_evidence_refs = [
            ref for ref in dict.fromkeys(evidence_refs)
            if isinstance(ref, str) and EVIDENCE_REF_PATTERN.fullmatch(ref)
        ][:30]

        # --- 2. HIỆU CHUẨN ĐỘ TIN CẬY (CONFIDENCE CALIBRATION - 5% ĐIỂM) ---
        has_conflicts = len(data_conflicts) > 0
        has_evidence = len(valid_evidence_refs) > 0

        if primary_issue == "insufficient_evidence":
            confidence = 0.45
        elif not has_evidence:
            confidence = 0.55
        elif has_conflicts:
            confidence = 0.75
        elif primary_issue in ("canceled_order_paid", "unavailable_order_paid", "duplicate_charge"):
            confidence = 0.95
        elif primary_issue in ("late_delivery_seller", "late_delivery_logistics"):
            confidence = 0.90
        elif primary_issue in ("unsupported_claim", "valid_split_payment"):
            confidence = 0.88
        else:
            confidence = 0.80

        # --- 3. KIỂM TRA TÍNH NHẤT QUÁN LOGIC (CROSS-FIELD CONSISTENCY - 10% ĐIỂM) ---
        # Quy tắc 1: Nếu no_action thì bắt buộc tiền hoàn = 0.0 và không có dòng hoàn tiền
        if case_status == "no_action":
            recommended_refund = 0.0
            refund_lines = []

        # Quy tắc 2: Nếu có refund_lines thì recommended_refund_brl phải khớp tổng tiền
        if refund_lines:
            total_lines_amount = sum(float(line.get("amount_brl", 0.0)) for line in refund_lines)
            recommended_refund = round(total_lines_amount, 2)
        elif recommended_refund > 0:
            refund_lines = [{
                "reason_code": f"{primary_issue}_resolution",
                "amount_brl": round(recommended_refund, 2),
                "entity_id": case_id,
            }]

        # Quy tắc 3: Chuẩn hóa Cause Code (pattern: ^[A-Z][A-Z0-9_]{2,79}$)
        cause_code = primary_issue.upper()
        if not CAUSE_CODE_PATTERN.fullmatch(cause_code):
            cause_code = "UNSPECIFIED_ISSUE"

        # Quy tắc 4: Chuẩn hóa Responsible Parties
        responsible_parties = policy_result.get("responsible_parties", [])
        if not responsible_parties:
            responsible_parties = [{"party_type": "unknown", "party_id": None}]

        # Quy tắc 5: Chuẩn hóa Resolution Actions (unique, max 8, non-empty)
        raw_actions = policy_result.get("resolution_actions", [])
        if not raw_actions:
            raw_actions = ["no_action" if case_status == "no_action" else "investigate_further"]
        clean_actions = [
            action[:80] for action in dict.fromkeys(raw_actions) if action.strip()
        ][:8]

        # Quy tắc 6: Chuẩn hóa Affected Entities (unique sets, max 20)
        entities = {
            "order_ids": [str(x)[:128] for x in dict.fromkeys(affected_entities.get("order_ids", [])) if x][:20],
            "item_ids": [str(x)[:128] for x in dict.fromkeys(affected_entities.get("item_ids", [])) if x][:20],
            "seller_ids": [str(x)[:128] for x in dict.fromkeys(affected_entities.get("seller_ids", [])) if x][:20],
            "payment_references": [str(x)[:128] for x in dict.fromkeys(affected_entities.get("payment_references", [])) if x][:20],
            "shipment_ids": [str(x)[:128] for x in dict.fromkeys(affected_entities.get("shipment_ids", [])) if x][:20],
        }

        # --- 4. ĐÓNG GÓI OUTPUT THEO SCHEMA CONTRACT ---
        output: dict[str, Any] = {
            "schema_version": "day09-l3a-output-v2",
            "case_id": case_id,
            "assessment": {
                "primary_issue": primary_issue,
                "case_status": case_status,
                "confidence": round(confidence, 2),
            },
            "affected_entities": entities,
            "root_cause_analysis": {
                "ranked_causes": [
                    {"cause_code": cause_code, "rank": 1}
                ][:5],
                "responsible_parties": responsible_parties[:5],
            },
            "evidence_refs": valid_evidence_refs,
            "data_conflicts": [
                {
                    "field": str(c["field"])[:100],
                    "sources": [str(s)[:80] for s in dict.fromkeys(c.get("sources", ["source_a", "source_b"]))][:5],
                    "selected_source": str(c["selected_source"])[:80] if c.get("selected_source") else None,
                    "resolution_code": str(c.get("resolution_code", "resolved"))[:80],
                }
                for c in data_conflicts
            ][:5],
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": round(recommended_refund, 2),
                "refund_lines": [
                    {
                        "reason_code": str(line["reason_code"])[:80],
                        "amount_brl": round(float(line["amount_brl"]), 2),
                        "entity_id": str(line["entity_id"])[:128] if line.get("entity_id") else None,
                    }
                    for line in refund_lines
                ][:10],
            },
            "resolution_actions": clean_actions,
        }

        # Bổ sung claim_assessments nếu có
        raw_claims = policy_result.get("claim_assessments", [])
        if raw_claims:
            clean_claims = []
            for claim in raw_claims[:5]:
                raw_claim_refs = claim.get("evidence_refs") or valid_evidence_refs
                claim_refs = [
                    ref for ref in dict.fromkeys(raw_claim_refs)
                    if isinstance(ref, str) and EVIDENCE_REF_PATTERN.fullmatch(ref)
                ][:30]
                clean_claims.append({
                    "claim_id": str(claim["claim_id"])[:64],
                    "verdict": claim.get("verdict", "insufficient_evidence"),
                    "confidence": round(float(claim.get("confidence", 0.5)), 2),
                    "evidence_refs": claim_refs,
                })
            output["claim_assessments"] = clean_claims

        # --- 5. BẢO MẬT & VALIDATE SCHEMA ---
        self.contracts.validate_output(output, f"verifier:{case_id}")

        return output
