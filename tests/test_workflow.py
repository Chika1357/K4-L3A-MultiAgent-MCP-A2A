from __future__ import annotations

from mcp.shared.exceptions import MCPError

from student_agent.cli import _transient_mcp_error, parser
from student_agent.workflow import _align_responsible_parties, _analyze


def test_responsible_seller_comes_from_order_items() -> None:
    parties = [{"party_type": "seller", "party_id": "seller-from-policy"}]
    items = [{"seller_id": "seller-from-order"}]

    assert _align_responsible_parties(parties, items, []) == [
        {"party_type": "seller", "party_id": "seller-from-order"}
    ]


def test_late_seller_takes_precedence_and_ambiguous_seller_is_unknown() -> None:
    parties = [{"party_type": "seller", "party_id": "seller-from-policy"}]
    items = [{"seller_id": "seller-a"}, {"seller_id": "seller-b"}]

    assert _align_responsible_parties(parties, items, ["seller-b"]) == [
        {"party_type": "seller", "party_id": "seller-b"}
    ]
    assert _align_responsible_parties(parties, items, []) == [
        {"party_type": "seller", "party_id": None}
    ]


def test_late_delivery_is_known_before_customer_receives_order() -> None:
    case = {
        "case_id": "CASE_004",
        "opened_at": "2018-04-04T09:00:00-03:00",
        "customer_request": {"claims": [{"topic": "late_delivery_logistics"}]},
    }
    collected = {
        "order": {
            "order": {
                "order_status": "delivered",
                "order_purchase_timestamp": "2018-03-23T09:00:00-03:00",
            },
            "items": [
                {
                    "order_item_id": "ITEM_1",
                    "seller_id": "SELLER_1",
                    "price": "79.00",
                    "freight_value": "18.00",
                    "shipping_limit_date": "2018-03-26T09:00:00-03:00",
                },
                {
                    "order_item_id": "ITEM_1",
                    "seller_id": "SELLER_1",
                    "price": "79.00",
                    "freight_value": "10.00",
                    "shipping_limit_date": "2018-02-11T09:00:00-03:00",
                },
            ],
        },
        "payment": {
            "payment_events": [
                {
                    "event_type": "captured",
                    "status": "confirmed",
                    "amount_brl": "16.00",
                    "event_at": "2018-03-23T10:00:00-03:00",
                }
            ],
            "refund_events": None,
            "payments": [{"payment_value": "16.00"}],
        },
        "shipment": {
            "summary": {
                "delivered_carrier_at": "2018-03-25T09:00:00-03:00",
                "delivered_customer_at": "2018-04-07T09:00:00-03:00",
                "estimated_delivery_at": "2018-04-02T09:00:00-03:00",
            }
        },
    }

    analysis = _analyze(case, collected)

    assert analysis["issue"] == "late_delivery_logistics"
    assert len(analysis["items"]) == 1
    assert analysis["late_sellers"] == []


def test_old_refund_cannot_override_current_split_payment() -> None:
    case = {
        "case_id": "CASE_005",
        "opened_at": "2018-05-05T09:00:00-03:00",
        "customer_request": {"claims": [{"topic": "valid_split_payment"}]},
    }
    collected = {
        "order": {
            "order": {
                "order_status": "delivered",
                "order_purchase_timestamp": "2018-04-23T09:00:00-03:00",
            },
            "items": [
                {
                    "order_item_id": "ITEM_1",
                    "seller_id": "SELLER_1",
                    "price": "79.00",
                    "freight_value": "10.00",
                    "shipping_limit_date": "2018-04-26T09:00:00-03:00",
                }
            ],
        },
        "payment": {
            "payment_events": [
                {
                    "event_type": "captured",
                    "status": "confirmed",
                    "amount_brl": "44.50",
                    "event_at": "2018-04-23T10:00:00-03:00",
                },
                {
                    "event_type": "captured",
                    "status": "confirmed",
                    "amount_brl": "44.50",
                    "event_at": "2018-04-23T11:00:00-03:00",
                },
            ],
            "refund_events": [
                {
                    "status": "failed",
                    "event_at": "2018-01-18T09:00:00-03:00",
                }
            ],
            "payments": [{"payment_value": "44.50"}, {"payment_value": "44.50"}],
        },
        "shipment": {
            "summary": {
                "delivered_carrier_at": "2018-04-25T09:00:00-03:00",
                "delivered_customer_at": "2018-05-02T09:00:00-03:00",
                "estimated_delivery_at": "2018-05-03T09:00:00-03:00",
            }
        },
    }

    analysis = _analyze(case, collected)

    assert analysis["issue"] == "valid_split_payment"
    assert str(analysis["paid_total"]) == "89.00"
    assert analysis["latest_refund"] is None


def test_runner_retries_only_transport_failure() -> None:
    dropped = MCPError(code=-32000, message="SSE stream ended without a response")
    forbidden = MCPError(code=-32000, message="403 Forbidden")

    assert _transient_mcp_error(ExceptionGroup("transport", [dropped]))
    assert not _transient_mcp_error(forbidden)


def test_artifact_root_can_be_selected_for_run_and_package() -> None:
    cli = parser()
    assert (
        cli.parse_args(["run", "--artifacts-root", "dist/isolated"]).artifacts_root
        == "dist/isolated"
    )
    assert (
        cli.parse_args(["package", "--artifacts-root", "dist/isolated"]).artifacts_root
        == "dist/isolated"
    )
    assert cli.parse_args(["run", "--workers", "4"]).workers == 4
