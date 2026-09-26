from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import OUTPUT_SCHEMA_VERSION, VARIANT_ID
from .cases import CaseSet
from .contracts import Contracts

SECRET_PATTERN = re.compile(r"sk-team-[A-Za-z0-9_-]{8,}")
EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")
MAX_FILE_BYTES = 1024 * 1024
MAX_SUBMISSION_BYTES = 12 * 1024 * 1024


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def build_manifest(case_set: CaseSet) -> dict[str, Any]:
    return {
        "schema_version": "day09-submission-manifest-v2",
        "competition_id": "day09-multiagent-mcp-a2a",
        "variant_id": VARIANT_ID,
        "case_set_version": case_set.version,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "trace_schema_version": "day09-trace-event-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "client": {"name": "day09-student-starter", "version": "0.1.0"},
    }


def expected_submission_members(case_set: CaseSet) -> list[str]:
    return [
        "manifest.json",
        "trace.jsonl",
        *[f"outputs/{case_id}.json" for case_id in case_set.case_ids],
    ]


def _evidence_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if EVIDENCE_REF_PATTERN.fullmatch(value) else set()
    if isinstance(value, list):
        refs: set[str] = set()
        for item in value:
            refs.update(_evidence_refs(item))
        return refs
    if isinstance(value, dict):
        refs: set[str] = set()
        for item in value.values():
            refs.update(_evidence_refs(item))
        return refs
    return set()


def validate_artifacts(
    root: Path, case_set: CaseSet, contracts: Contracts
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    outputs_root = root / "outputs"
    actual = {path.stem: path for path in outputs_root.glob("*.json") if path.is_file()}
    expected = set(case_set.case_ids)
    if set(actual) != expected:
        missing = sorted(expected - set(actual))
        extra = sorted(set(actual) - expected)
        raise ValueError(f"outputs do not match case-set; missing={missing}, extra={extra}")

    outputs: dict[str, dict[str, Any]] = {}
    for case_id in case_set.case_ids:
        output = _json_object(actual[case_id])
        contracts.validate_output(output, f"outputs/{case_id}.json")
        if output.get("case_id") != case_id:
            raise ValueError(f"outputs/{case_id}.json has a mismatched case_id")
        outputs[case_id] = output

    trace_path = root / "traces" / "trace.jsonl"
    try:
        trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("traces/trace.jsonl is missing or not UTF-8") from exc
    normalized_lines: list[str] = []
    seen_events: set[str] = set()
    event_types_by_case: dict[str, list[str]] = {case_id: [] for case_id in case_set.case_ids}
    consumed_refs_by_case: dict[str, set[str]] = {case_id: set() for case_id in case_set.case_ids}
    for number, line in enumerate(trace_lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"traces/trace.jsonl:{number}: invalid JSON") from exc
        contracts.validate_trace(event, f"traces/trace.jsonl:{number}")
        if event["case_id"] not in expected:
            raise ValueError(f"traces/trace.jsonl:{number}: case is outside this case-set")
        if event["event_id"] in seen_events:
            raise ValueError(f"traces/trace.jsonl:{number}: duplicate event_id")
        seen_events.add(event["event_id"])
        event_types_by_case[event["case_id"]].append(event["event_type"])
        if event["event_type"] == "tool_result_consumed":
            consumed_refs_by_case[event["case_id"]].update(event.get("evidence_refs", []))
        normalized_lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")))

    for case_id in case_set.case_ids:
        event_types = event_types_by_case[case_id]
        if event_types.count("case_received") != 1:
            raise ValueError(f"{case_id}: trace must contain exactly one case_received event")
        if event_types.count("case_finalized") != 1:
            raise ValueError(f"{case_id}: trace must contain exactly one case_finalized event")
        if event_types.index("case_received") > event_types.index("case_finalized"):
            raise ValueError(f"{case_id}: case_finalized appears before case_received")
        for required in ("tool_result_consumed", "policy_decided", "verification_completed"):
            if required not in event_types:
                raise ValueError(f"{case_id}: trace is missing {required}")
        cited_refs = _evidence_refs(outputs[case_id])
        missing_refs = sorted(cited_refs - consumed_refs_by_case[case_id])
        if missing_refs:
            raise ValueError(
                f"{case_id}: output cites evidence refs without same-case "
                f"tool_result_consumed events: {missing_refs}"
            )

    serialized = [json.dumps(value, ensure_ascii=False) for value in outputs.values()]
    if SECRET_PATTERN.search("\n".join([*serialized, *normalized_lines])):
        raise ValueError("a Team API Key appears in output or trace")
    return outputs, normalized_lines


def validate_submission_zip(path: Path, case_set: CaseSet, contracts: Contracts) -> None:
    expected = expected_submission_members(case_set)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            actual = archive.namelist()
            if len(actual) != len(set(actual)):
                raise ValueError("submission ZIP contains duplicate member names")
            if actual != expected:
                missing = sorted(set(expected) - set(actual))
                extra = sorted(set(actual) - set(expected))
                raise ValueError(
                    "submission ZIP must contain only manifest.json, trace.jsonl, "
                    f"and outputs/<case_id>.json; missing={missing}, extra={extra}"
                )
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            contracts.validate_manifest(manifest)
            for case_id in case_set.case_ids:
                output = json.loads(archive.read(f"outputs/{case_id}.json").decode("utf-8"))
                contracts.validate_output(output, f"outputs/{case_id}.json")
                if output.get("case_id") != case_id:
                    raise ValueError(f"outputs/{case_id}.json has a mismatched case_id")
            trace_lines = archive.read("trace.jsonl").decode("utf-8").splitlines()
            for number, line in enumerate(trace_lines, 1):
                if line.strip():
                    contracts.validate_trace(json.loads(line), f"trace.jsonl:{number}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{path}: not a valid ZIP file") from exc


def package_submission(root: Path, destination: Path, artifacts_root: Path | None = None) -> Path:
    from .cases import load_case_set

    root = root.resolve()
    case_set = load_case_set(root)
    contracts = Contracts(root / "contracts" / "schemas")
    outputs, trace_lines = validate_artifacts(artifacts_root or root, case_set, contracts)
    manifest = build_manifest(case_set)
    contracts.validate_manifest(manifest)

    payloads = {
        "manifest.json": json.dumps(manifest, separators=(",", ":")).encode(),
        "trace.jsonl": ("\n".join(trace_lines) + ("\n" if trace_lines else "")).encode(),
        **{
            f"outputs/{case_id}.json": json.dumps(
                outputs[case_id], ensure_ascii=False, separators=(",", ":")
            ).encode()
            for case_id in case_set.case_ids
        },
    }
    oversized = [name for name, payload in payloads.items() if len(payload) > MAX_FILE_BYTES]
    if oversized:
        raise ValueError(f"submission files exceed 1 MB: {oversized}")
    if sum(map(len, payloads.values())) > MAX_SUBMISSION_BYTES:
        raise ValueError("submission exceeds the 12 MB uncompressed limit")

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    validate_submission_zip(destination, case_set, contracts)
    return destination
