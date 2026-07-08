from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    entity: str | None = None
    entity_id: int | str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "entity": self.entity,
            "entityId": self.entity_id,
        }


@dataclass
class ValidationReport:
    ok: bool
    counts: dict[str, int]
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    infos: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "infos": len(self.infos),
            },
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "infos": [issue.to_dict() for issue in self.infos],
        }

