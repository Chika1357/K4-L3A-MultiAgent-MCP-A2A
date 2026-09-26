from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from student_agent import OUTPUT_SCHEMA_VERSION, VARIANT_ID
from student_agent.cases import CaseSet
from student_agent.contracts import Contracts
from student_agent.submission import (
    build_manifest,
    expected_submission_members,
    validate_artifacts,
    validate_submission_zip,
)


def _output(case_id: str, refs: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "case_id": case_id,
        "assessment": {
            "primary_issue": "unsupported_claim",
            "case_status": "no_action",
            "confidence": 0.8,
        },
        "affected_entities": {
            "order_ids": ["ORDER_1"],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "UNSUPPORTED_CLAIM", "rank": 1}],
            "responsible_parties": [{"party_type": "customer", "party_id": None}],
        },
        "evidence_refs": refs or [],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 0,
            "refund_lines": [],
        },
        "resolution_actions": ["document_no_action"],
    }


def _event(event_id: str, case_id: str, event_type: str, refs: list[str] | None = None) -> str:
    return json.dumps(
        {
            "schema_version": "day09-trace-event-v1",
            "event_id": event_id,
            "case_id": case_id,
            "event_type": event_type,
            "occurred_at": "2026-01-01T00:00:00Z",
            "actor": "coordinator",
            "evidence_refs": refs or [],
        }
    )


def test_validate_artifacts_rejects_unconsumed_output_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = CaseSet("test-v1", VARIANT_ID, ("CASE_001",), {})
    cited = "ev_AAAAAAAAAAAAAAAAAAAA"
    consumed = "ev_BBBBBBBBBBBBBBBBBBBB"

    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "CASE_001.json").write_text(
        json.dumps(_output("CASE_001", [cited])), encoding="utf-8"
    )
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "trace.jsonl").write_text(
        "\n".join(
            [
                _event("evt_AAAAAAAAAAAA", "CASE_001", "case_received"),
                _event("evt_BBBBBBBBBBBB", "CASE_001", "tool_result_consumed", [consumed]),
                _event("evt_CCCCCCCCCCCC", "CASE_001", "policy_decided"),
                _event("evt_DDDDDDDDDDDD", "CASE_001", "verification_completed"),
                _event("evt_EEEEEEEEEEEE", "CASE_001", "case_finalized"),
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="without same-case tool_result_consumed"):
        validate_artifacts(tmp_path, case_set, contracts)


def test_submission_zip_must_match_required_members(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = CaseSet("test-v1", VARIANT_ID, ("CASE_001",), {})
    archive_path = tmp_path / "submission.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(build_manifest(case_set)))
        archive.writestr("trace.jsonl", "")
        archive.writestr("outputs/CASE_001.json", json.dumps(_output("CASE_001")))
        archive.writestr("README.md", "extra")

    assert expected_submission_members(case_set) == [
        "manifest.json",
        "trace.jsonl",
        "outputs/CASE_001.json",
    ]
    with pytest.raises(ValueError, match="must contain only"):
        validate_submission_zip(archive_path, case_set, contracts)
