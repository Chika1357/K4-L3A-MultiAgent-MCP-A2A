from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx2
from mcp.shared.exceptions import MCPError

from .cases import load_case_set
from .config import Settings
from .contracts import Contracts
from .mcp_gateway import connect_gateway
from .submission import package_submission, validate_artifacts
from .trace import TraceWriter
from .workflow import solve_case


def _root(value: str) -> Path:
    return Path(value).resolve()


def _transient_mcp_error(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_transient_mcp_error(item) for item in exc.exceptions)
    if isinstance(exc, (TimeoutError, ConnectionError, httpx2.TransportError)):
        return True
    if isinstance(exc, RuntimeError) and "MCP tool" in str(exc) and "failed" in str(exc):
        return True
    return isinstance(exc, MCPError) and (
        "SSE stream ended without a response" in str(exc) or "Connection closed" in str(exc)
    )


async def _show_tools(root: Path) -> None:
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gateway:
        for tool in await gateway.list_tools():
            print(tool)


async def _run(root: Path, artifacts_root: Path | None = None, workers: int = 1) -> None:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    settings = Settings.load(root)
    case_set = load_case_set(root)
    contracts = Contracts(root / "contracts" / "schemas")
    artifacts_root = (artifacts_root or root).resolve()
    output_root = artifacts_root / "outputs"
    trace_path = artifacts_root / "traces" / "trace.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    shard_root = trace_path.parent / "cases"
    if workers > 1:
        shard_root.mkdir(parents=True, exist_ok=True)
        for stale in shard_root.glob("*.jsonl"):
            stale.unlink()

    async def run_case(case_id: str, case_trace_path: Path) -> None:
        target = output_root / f"{case_id}.json"
        if target.exists():
            print(f"{case_id}: skipped", flush=True)
            return
        case = case_set.cases[case_id]
        trace = TraceWriter(case_trace_path, contracts)
        for attempt in range(5):
            trace_offset = case_trace_path.stat().st_size if case_trace_path.exists() else 0
            try:
                async with connect_gateway(
                    settings.mcp_endpoint, settings.team_api_key, contracts
                ) as gateway:
                    if not await gateway.list_tools():
                        raise RuntimeError("MCP Gateway returned no tools")
                    trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
                    output = await solve_case(case, gateway, trace)
                contracts.validate_output(output, f"outputs/{case_id}.json")
                if output.get("case_id") != case_id:
                    raise ValueError(f"solver returned a mismatched case_id for {case_id}")
                temporary = target.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                temporary.replace(target)
                trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")
                print(f"{case_id}: complete", flush=True)
                break
            except Exception as exc:
                if case_trace_path.exists():
                    with case_trace_path.open("r+b") as handle:
                        handle.truncate(trace_offset)
                if attempt == 4 or not _transient_mcp_error(exc):
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    if workers == 1:
        for case_id in case_set.case_ids:
            await run_case(case_id, trace_path)
    else:
        semaphore = asyncio.Semaphore(workers)

        async def guarded(case_id: str) -> None:
            async with semaphore:
                await run_case(case_id, shard_root / f"{case_id}.jsonl")

        await asyncio.gather(*(guarded(case_id) for case_id in case_set.case_ids))
        with trace_path.open("w", encoding="utf-8") as combined:
            for case_id in case_set.case_ids:
                combined.write((shard_root / f"{case_id}.jsonl").read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Day09 L3A student workflow")
    result.add_argument("--root", default=".", help="repository root (default: current directory)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-inputs", help="validate case-set.json and all 100 inputs")
    commands.add_parser("mcp-tools", help="authenticate and list discovered MCP tools")
    run = commands.add_parser("run", help="run the implemented workflow for all cases")
    run.add_argument("--artifacts-root", default=".")
    run.add_argument("--workers", type=int, default=1)
    validate = commands.add_parser("validate", help="validate outputs and observable trace")
    validate.add_argument("--artifacts-root", default=".")
    package = commands.add_parser("package", help="validate and build the submission ZIP")
    package.add_argument("--output", default="dist/submission.zip")
    package.add_argument("--artifacts-root", default=".")
    return result


def main() -> None:
    args = parser().parse_args()
    root = _root(args.root)
    try:
        if args.command == "validate-inputs":
            case_set = load_case_set(root)
            print(
                f"OK: {case_set.variant_id} / {case_set.version} / {len(case_set.case_ids)} cases"
            )
        elif args.command == "mcp-tools":
            asyncio.run(_show_tools(root))
        elif args.command == "run":
            asyncio.run(_run(root, root / args.artifacts_root, args.workers))
        elif args.command == "validate":
            case_set = load_case_set(root)
            contracts = Contracts(root / "contracts" / "schemas")
            _, trace = validate_artifacts(root / args.artifacts_root, case_set, contracts)
            print(f"OK: {len(case_set.case_ids)} outputs / {len(trace)} trace events")
        elif args.command == "package":
            destination = package_submission(root, root / args.output, root / args.artifacts_root)
            print(f"OK: {destination}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
