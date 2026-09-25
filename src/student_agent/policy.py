from __future__ import annotations

import re
from typing import Any

VALID_PRIMARY_ISSUES = {
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "payment_mismatch",
    "duplicate_charge",
    "refund_pending",
    "refund_failed",
    "unsupported_claim",
    "insufficient_evidence",
}

VALID_PARTY_TYPES = {
    "seller",
    "platform",
    "logistics_provider",
    "payment_provider",
    "customer",
    "unknown",
}


class PolicyAgent:
    """Agent chịu trách nhiệm phân xử nghiệp vụ, quy trách nhiệm và xác định tài chính."""

    def evaluate(self, case: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
        """
        Đánh giá tình huống dựa trên case context và facts thu thập từ MCP.

        Args:
            case: Dictionary chứa case_id, customer_message, claims, entities...
            facts: Dictionary chứa dữ liệu thực tế từ MCP (order, payment, shipment, seller, policy...)

        Returns:
            Dictionary chứa các trường sơ bộ để Verifier kiểm định và đóng gói.
        """
        case_id = case.get("case_id", "")
        customer_message = str(case.get("customer_message", "")).lower()

        # Trích xuất dữ liệu thực tế từ facts
        order_status = facts.get("order_status")  # e.g., 'canceled', 'unavailable', 'delivered', 'invoiced'
        payment_info = facts.get("payment_info", {})
        shipment_info = facts.get("shipment_info", {})
        refund_info = facts.get("refund_info", {})

        total_paid = float(payment_info.get("total_paid", 0.0))
        order_total = float(facts.get("order_total", total_paid))
        seller_id = facts.get("seller_id")
        carrier_id = shipment_info.get("carrier_id")

        primary_issue = "insufficient_evidence"
        case_status = "needs_investigation"
        responsible_parties: list[dict[str, Any]] = []
        recommended_refund = 0.0
        refund_lines: list[dict[str, Any]] = []
        resolution_actions: list[str] = []
        data_conflicts: list[dict[str, Any]] = list(facts.get("data_conflicts", []))

        # --- 1. KIỂM TRA CÁC CASE VỀ REFUND TRƯỚC ---
        if refund_info.get("status") == "failed" or refund_info.get("is_failed"):
            primary_issue = "refund_failed"
            case_status = "action_required"
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            refund_amount = float(refund_info.get("amount_brl", total_paid))
            recommended_refund = refund_amount
            refund_lines.append({
                "reason_code": "retry_failed_refund",
                "amount_brl": round(refund_amount, 2),
                "entity_id": case_id,
            })
            resolution_actions.extend(["retry_refund_transaction", "notify_customer_refund_update"])

        elif refund_info.get("status") == "pending" or refund_info.get("is_pending"):
            primary_issue = "refund_pending"
            case_status = "action_required"
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            recommended_refund = 0.0  # Đang chờ xử lý từ cổng thanh toán, không cấp thêm hoàn tiền kép
            resolution_actions.extend(["monitor_refund_status", "notify_customer_processing_delay"])

        # --- 2. KIỂM TRA ĐƠN BỊ HỦY / HẾT HÀNG NHƯNG ĐÃ TRẢ TIỀN ---
        elif order_status == "canceled" and total_paid > 0:
            primary_issue = "canceled_order_paid"
            case_status = "action_required"
            responsible_parties.append({"party_type": "platform", "party_id": None})
            recommended_refund = total_paid
            refund_lines.append({
                "reason_code": "canceled_order_full_refund",
                "amount_brl": round(total_paid, 2),
                "entity_id": case_id,
            })
            resolution_actions.extend(["process_full_refund", "close_canceled_order"])

        elif order_status in ("unavailable", "out_of_stock") and total_paid > 0:
            primary_issue = "unavailable_order_paid"
            case_status = "action_required"
            responsible_parties.append({"party_type": "platform", "party_id": None})
            if seller_id:
                responsible_parties.append({"party_type": "seller", "party_id": str(seller_id)})
            recommended_refund = total_paid
            refund_lines.append({
                "reason_code": "unavailable_inventory_refund",
                "amount_brl": round(total_paid, 2),
                "entity_id": case_id,
            })
            resolution_actions.extend(["process_full_refund", "update_inventory_status"])

        # --- 3. KIỂM TRA THANH TOÁN (DUPLICATE CHARGE, MISMATCH, SPLIT) ---
        elif payment_info.get("is_duplicate"):
            primary_issue = "duplicate_charge"
            case_status = "action_required"
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            overpaid = float(payment_info.get("overpaid_amount", total_paid - order_total))
            if overpaid <= 0:
                overpaid = float(payment_info.get("duplicate_amount", total_paid / 2))
            recommended_refund = overpaid
            refund_lines.append({
                "reason_code": "duplicate_charge_reversal",
                "amount_brl": round(overpaid, 2),
                "entity_id": case_id,
            })
            resolution_actions.extend(["refund_duplicate_payment", "notify_customer_charge_reversed"])

        elif payment_info.get("is_mismatch") or (abs(total_paid - order_total) > 0.01 and not payment_info.get("is_split")):
            primary_issue = "payment_mismatch"
            case_status = "action_required"
            responsible_parties.append({"party_type": "platform", "party_id": None})
            diff = total_paid - order_total
            if diff > 0:
                recommended_refund = diff
                refund_lines.append({
                    "reason_code": "overpayment_adjustment",
                    "amount_brl": round(diff, 2),
                    "entity_id": case_id,
                })
                resolution_actions.extend(["refund_overpayment", "reconcile_payment_record"])
            else:
                resolution_actions.extend(["request_balance_settlement", "reconcile_payment_record"])

        elif payment_info.get("is_split") and payment_info.get("is_valid_split"):
            # Khách hiểu lầm việc thanh toán chia thành nhiều đợt/thẻ là bị trừ 2 lần
            primary_issue = "valid_split_payment"
            case_status = "no_action"
            responsible_parties.append({"party_type": "customer", "party_id": None})
            recommended_refund = 0.0
            resolution_actions.extend(["clarify_split_payment_schedule", "no_refund_required"])
            if "duplicate" in customer_message or "cobrança dupla" in customer_message:
                data_conflicts.append({
                    "field": "payment.transaction_count",
                    "sources": ["customer_statement", "mcp.payment_gateway"],
                    "selected_source": "mcp.payment_gateway",
                    "resolution_code": "verified_legitimate_split_payment",
                })

        # --- 4. KIỂM TRA GIAO HÀNG TRỄ (LATE DELIVERY) ---
        elif shipment_info.get("is_late"):
            if shipment_info.get("late_by_seller"):
                primary_issue = "late_delivery_seller"
                case_status = "action_required"
                responsible_parties.append({
                    "party_type": "seller",
                    "party_id": str(seller_id) if seller_id else None,
                })
                compensation = float(shipment_info.get("delay_compensation_brl", 0.0))
                if compensation > 0:
                    recommended_refund = compensation
                    refund_lines.append({
                        "reason_code": "seller_dispatch_delay_compensation",
                        "amount_brl": round(compensation, 2),
                        "entity_id": str(seller_id) if seller_id else case_id,
                    })
                resolution_actions.extend(["issue_seller_warning", "track_fulfillment_sla"])

            else:
                primary_issue = "late_delivery_logistics"
                case_status = "action_required"
                responsible_parties.append({
                    "party_type": "logistics_provider",
                    "party_id": str(carrier_id) if carrier_id else None,
                })
                shipping_refund = float(shipment_info.get("freight_value", 0.0))
                if shipping_refund > 0:
                    recommended_refund = shipping_refund
                    refund_lines.append({
                        "reason_code": "carrier_delay_freight_refund",
                        "amount_brl": round(shipping_refund, 2),
                        "entity_id": str(carrier_id) if carrier_id else case_id,
                    })
                resolution_actions.extend(["expedite_transit_delivery", "claim_carrier_sla_penalty"])

        # --- 5. KHIẾU NẠI KHÔNG CÓ CĂN CỨ HOẶC ĐƠN ĐÃ HOÀN TẤT BÌNH THƯỜNG ---
        elif order_status == "delivered" and not shipment_info.get("is_late"):
            primary_issue = "unsupported_claim"
            case_status = "no_action"
            responsible_parties.append({"party_type": "customer", "party_id": None})
            recommended_refund = 0.0
            resolution_actions.extend(["provide_delivery_proof", "reject_unsupported_claim"])
            data_conflicts.append({
                "field": "order.delivery_status",
                "sources": ["customer_claim", "mcp.carrier_tracking"],
                "selected_source": "mcp.carrier_tracking",
                "resolution_code": "verified_timely_delivery",
            })

        # --- 6. FALLBACK KHI THIẾU THÔNG TIN ---
        else:
            primary_issue = "insufficient_evidence"
            case_status = "needs_investigation"
            responsible_parties.append({"party_type": "unknown", "party_id": None})
            recommended_refund = 0.0
            resolution_actions.append("request_additional_documents")

        # Đảm bảo tính nhất quán: Nếu no_action thì recommended_refund = 0.0
        if case_status == "no_action":
            recommended_refund = 0.0
            refund_lines = []

        # Tự động thẩm định các claims từ customer_request nếu facts chưa cung cấp
        claim_assessments = facts.get("claim_assessments")
        if claim_assessments is None:
            claim_assessments = []
            customer_claims = case.get("customer_request", {}).get("claims", [])
            for c in customer_claims:
                cid = c.get("claim_id")
                topic = c.get("topic", "")
                if not cid:
                    continue
                verdict = "insufficient_evidence"
                if topic == primary_issue:
                    verdict = "supported" if case_status == "action_required" else "unsupported"
                elif topic in ("requested_full_refund", "full_refund", "refund_requested"):
                    verdict = "supported" if recommended_refund > 0 else "unsupported"
                elif "late_delivery" in topic:
                    verdict = "supported" if "late_delivery" in primary_issue else "unsupported"
                elif primary_issue == "unsupported_claim":
                    verdict = "unsupported"
                else:
                    verdict = "partially_supported" if recommended_refund > 0 else "unsupported"

                claim_assessments.append({
                    "claim_id": cid,
                    "verdict": verdict,
                    "confidence": 0.90 if verdict in ("supported", "unsupported") else 0.70,
                    "evidence_refs": facts.get("evidence_refs", []),
                })

        return {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "responsible_parties": responsible_parties[:5],
            "recommended_refund_brl": round(float(recommended_refund), 2),
            "refund_lines": refund_lines[:10],
            "resolution_actions": list(dict.fromkeys(resolution_actions))[:8],
            "data_conflicts": data_conflicts[:5],
            "claim_assessments": claim_assessments[:5],
        }
