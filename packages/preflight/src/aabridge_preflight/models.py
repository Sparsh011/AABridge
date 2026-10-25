"""Versioned diagnostic output, not the future task/event protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Status = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    summary: str
    remedy: str | None = None


@dataclass(frozen=True)
class Report:
    checks: tuple[Check, ...]

    @property
    def has_errors(self) -> bool:
        return any(check.status == "error" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "milestone": "foundation",
            "report_scope": "prerequisites_only; project execution gates are not inspected",
            "foundation_ready": not self.has_errors,
            "worker_execution_enabled": False,
            "verification": {
                "agent_authentication": "not_verified",
                "worker_isolation": "not_verified",
                "real_local_agents": "not_verified",
                "real_two_device_agents": "not_verified",
            },
            "checks": [asdict(check) for check in self.checks],
        }
