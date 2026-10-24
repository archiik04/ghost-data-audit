"""Loading and validating the participant-facing CSV inputs."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .model import parse_time

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset"

REQUEST_COLUMNS = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
REQUIRED = {
    "user_identities.csv": ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
    "user_audit_log.csv": ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
    "system_activity.csv": ["activity_id", "event_time", "event_type", "source_system", "target_system",
                            "resource_id", "reported_result", "details"],
    "deletion_rules.csv": ["rule_id", "data_category", "condition", "required_action"],
    "evidence_files.csv": ["file_id", "file_path", "origin", "captured_at"],
}


class InputError(Exception):
    """Raised when a required input is missing or structurally invalid."""


def read_csv(path: Path, required: list[str]) -> list[dict]:
    if not path.exists():
        raise InputError(f"missing input file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [c for c in required if c not in header]
        if missing:
            raise InputError(f"{path.name}: missing columns {missing}")
        return [{k: (v if v is not None else "") for k, v in row.items() if k is not None} for row in reader]


@dataclass
class Request:
    case_id: str
    email: str
    requested_at: object
    reported_status: str
    reported_at: object
    audit_at: object
    request_text: str


@dataclass
class Dataset:
    root: Path
    identities: list
    audit_log: list
    activity: list
    rules: list
    evidence_files: list
    warnings: list = field(default_factory=list)


def load_requests(path: Path) -> tuple[list[Request], list[str]]:
    rows = read_csv(path, REQUEST_COLUMNS)
    seen: set[str] = set()
    out: list[Request] = []
    warnings: list[str] = []
    for row in rows:
        cid = row["case_id"].strip()
        if not cid:
            warnings.append("request row with empty case_id skipped")
            continue
        if cid in seen:
            warnings.append(f"duplicate case_id {cid}: first occurrence kept")
            continue
        seen.add(cid)
        audit_at = parse_time(row["audit_at"])
        if audit_at is None:
            warnings.append(f"{cid}: unparseable audit_at {row['audit_at']!r}")
        out.append(Request(
            case_id=cid,
            email=row["email"].strip(),
            requested_at=parse_time(row["requested_at"]),
            reported_status=row["reported_status"].strip().upper(),
            reported_at=parse_time(row["reported_at"]),
            audit_at=audit_at,
            request_text=row["request_text"] or "",
        ))
    return out, warnings


def load_dataset(root: Optional[Path] = None) -> Dataset:
    root = Path(root) if root else DATASET
    tables = {name: read_csv(root / name, cols) for name, cols in REQUIRED.items()}
    return Dataset(
        root=root,
        identities=tables["user_identities.csv"],
        audit_log=tables["user_audit_log.csv"],
        activity=tables["system_activity.csv"],
        rules=tables["deletion_rules.csv"],
        evidence_files=tables["evidence_files.csv"],
    )
