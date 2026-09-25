from __future__ import annotations

import asyncio
from typing import Any

import httpx2

from student_agent.mcp_gateway import EvidenceGateway
from student_agent.trace import TraceWriter


class EvidenceCollector:
    """Keep MCP results and trace events scoped to one case."""

    def __init__(
        self,
        case_id: str,
        gateway: EvidenceGateway,
        trace: TraceWriter,
        available_tools: set[str],
    ) -> None:
        self.case_id = case_id
        self.gateway = gateway
        self.trace = trace
        self.available_tools = available_tools
        self.evidence_by_tool: dict[str, dict[str, Any]] = {}

    async def call(self, tool_name: str, *, actor: str, domain: str, **arguments: str) -> Any:
        if tool_name not in self.available_tools:
            raise RuntimeError(f"MCP Gateway does not offer {tool_name}")
        if tool_name in self.evidence_by_tool:
            raise ValueError(f"{tool_name} was already called for {self.case_id}")

        for attempt in range(3):
            try:
                evidence = await self.gateway.call(tool_name, case_id=self.case_id, **arguments)
                break
            except (TimeoutError, ConnectionError, httpx2.TimeoutException, httpx2.NetworkError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))

        if evidence.get("domain") != domain:
            raise ValueError(f"{tool_name} returned an unexpected evidence domain")
        evidence_ref = evidence["evidence_ref"]
        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool_name,
            evidence_refs=[evidence_ref],
        )
        self.evidence_by_tool[tool_name] = evidence
        return evidence["data"]

    def refs(self) -> list[str]:
        return list(
            dict.fromkeys(evidence["evidence_ref"] for evidence in self.evidence_by_tool.values())
        )
