"""Output serialization and the final schema validator."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .audit import FINDING_ORDER, PHASE, REMEDIATION_ORDER, STATE, VERIFICATION_ORDER

HEADER = ["case_id", "deletion_status", "steps_json"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OP_RANK = {op: i for i, op in enumerate(FINDING_ORDER + REMEDIATION_ORDER + VERIFICATION_ORDER)}


def serialize(steps: list) -> str:
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def write_output(path: Path, rows: list[tuple[str, str, list]]) -> None:
    rows = sorted(rows, key=lambda r: case_sort_key(r[0]))
    tmp = Path(path).with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(HEADER)
        for cid, status, steps in rows:
            w.writerow([cid, status, serialize(steps)])
    tmp.replace(path)  # atomic: an interrupted run never leaves a half-written output.csv


def case_sort_key(case_id: str):
    head, _, num = case_id.rpartition("_")
    return (head, int(num)) if num.isdigit() else (case_id, -1)


def validate_output(path: Path, expected_case_ids: list[str], known_evidence: set) -> list[str]:
    """Read the CSV back and check every contract rule. Returns a list of violations."""
    errors: list[str] = []
    with Path(path).open(encoding="utf-8", newline="") as h:
        reader = csv.reader(h)
        header = next(reader, None)
        if header != HEADER:
            errors.append(f"header {header} != {HEADER}")
        rows = list(reader)
    ids = [r[0] for r in rows if r]
    if len(ids) != len(set(ids)):
        errors.append("duplicate case_id rows")
    if set(ids) != set(expected_case_ids):
        missing = sorted(set(expected_case_ids) - set(ids))[:5]
        extra = sorted(set(ids) - set(expected_case_ids))[:5]
        errors.append(f"case coverage mismatch missing={missing} extra={extra}")
    if ids != sorted(ids, key=case_sort_key):
        errors.append("rows not sorted by case_id")
    for r in rows:
        if len(r) != 3:
            errors.append(f"{r[:1]}: expected 3 columns, got {len(r)}")
            continue
        cid, status, raw = r
        if status not in STATUSES:
            errors.append(f"{cid}: bad status {status}")
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{cid}: invalid JSON {exc}")
            continue
        if not isinstance(steps, list):
            errors.append(f"{cid}: steps_json is not an array")
            continue
        if status == "NOT_APPLICABLE" and steps:
            errors.append(f"{cid}: NOT_APPLICABLE must have []")
        if status == "INCOMPLETE" and not steps:
            errors.append(f"{cid}: INCOMPLETE with no steps")
        if status == "COMPLETE" and not steps:
            # contract-valid JSON, but no evidence-backed check: surfaced, not fatal
            errors.append(f"warning: {cid}: COMPLETE with no evidence-backed verification step")
        prev = (-1, -1, "", "")
        for i, s in enumerate(steps, 1):
            if set(s) != STEP_FIELDS:
                errors.append(f"{cid}: step {i} fields {sorted(s)}")
                continue
            if s["step_no"] != i:
                errors.append(f"{cid}: step_no {s['step_no']} != {i}")
            op = s["operation"]
            if op not in PHASE or PHASE[op] != s["phase"] or STATE[op] != s["state"]:
                errors.append(f"{cid}: step {i} invalid op/phase/state {op}/{s['phase']}/{s['state']}")
                continue
            key = (PHASE_RANK[s["phase"]], OP_RANK[op], s["system_id"], s["resource_id"])
            if key < prev:
                errors.append(f"{cid}: step {i} out of order")
            prev = key
            if status == "COMPLETE" and s["phase"] != "VERIFICATION":
                errors.append(f"{cid}: COMPLETE row contains {s['phase']}")
            if status == "INCOMPLETE" and s["phase"] != "FINDING" and s["evidence_id"]:
                errors.append(f"{cid}: future step {i} must have empty evidence_id")
            if s["phase"] == "FINDING" and s["evidence_id"] not in known_evidence:
                errors.append(f"{cid}: finding cites unknown evidence {s['evidence_id']!r}")
            if status == "COMPLETE" and s["evidence_id"] and s["evidence_id"] not in known_evidence:
                errors.append(f"{cid}: verification cites unknown evidence {s['evidence_id']!r}")
        if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
            errors.append(f"{cid}: INCOMPLETE without findings")
    return errors
