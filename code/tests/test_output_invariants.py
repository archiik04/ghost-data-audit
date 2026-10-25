"""Invariants checked on the real submission output (skipped if output.csv is absent).

These are contract-level properties of `output.csv` against the participant data, not
comparisons with any answer key.
"""
import csv
import json
from pathlib import Path

import pytest

from ghost.data import DATASET
from ghost.model import parse_time, system_for

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output.csv"
pytestmark = pytest.mark.skipif(not OUTPUT.exists(), reason="output.csv not generated yet")


def _rows():
    return list(csv.DictReader(OUTPUT.open(encoding="utf-8")))


def _times():
    t = {r["file_id"]: parse_time(r["captured_at"]) for r in csv.DictReader(open(DATASET / "evidence_files.csv", encoding="utf-8"))}
    t.update({r["activity_id"]: parse_time(r["event_time"]) for r in csv.DictReader(open(DATASET / "system_activity.csv", encoding="utf-8"))})
    t.update({r["audit_id"]: parse_time(r["event_time"]) for r in csv.DictReader(open(DATASET / "user_audit_log.csv", encoding="utf-8"))})
    return t


def test_every_request_has_exactly_one_row_in_order():
    req = [r["case_id"] for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))]
    got = [r["case_id"] for r in _rows()]
    assert sorted(got) == sorted(req) and len(got) == len(set(got)) == 518


def test_no_citation_postdates_audit_at():
    audit = {r["case_id"]: parse_time(r["audit_at"]) for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))}
    times = _times()
    late = [(r["case_id"], s["evidence_id"]) for r in _rows() for s in json.loads(r["steps_json"])
            if s["evidence_id"] and times[s["evidence_id"]] > audit[r["case_id"]]]
    assert late == []


def test_complete_rows_cite_evidence_for_every_check():
    bad = [(r["case_id"], s["resource_id"]) for r in _rows() if r["deletion_status"] == "COMPLETE"
           for s in json.loads(r["steps_json"]) if not s["evidence_id"]]
    assert bad == []


def test_resource_prefix_matches_system():
    bad = [(r["case_id"], s["resource_id"], s["system_id"]) for r in _rows() for s in json.loads(r["steps_json"])
           if system_for(s["resource_id"]) != s["system_id"]]
    assert bad == []


def test_every_remediated_item_is_verified_and_findings_are_remediated():
    pair = {"DELETE_RECORD": "VERIFY_RECORD_ABSENT", "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
            "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED", "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
            "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED"}
    for r in _rows():
        st = json.loads(r["steps_json"])
        fixes = {(s["operation"], s["resource_id"]) for s in st if s["phase"] == "REMEDIATION"}
        checks = {(s["operation"], s["resource_id"]) for s in st if s["phase"] == "VERIFICATION"}
        found = {s["resource_id"] for s in st if s["phase"] == "FINDING"}
        assert all((pair[op], rid) in checks for op, rid in fixes), r["case_id"]
        assert found == {rid for _, rid in fixes}, r["case_id"]


def test_retained_records_are_never_deleted():
    bad = [(r["case_id"], s["resource_id"]) for r in _rows() for s in json.loads(r["steps_json"])
           if s["operation"] == "DELETE_RECORD" and s["system_id"] in ("security_service", "legal_service", "backup_service")]
    assert bad == []


@pytest.mark.skipif(not (DATASET / "evidence_files.csv").exists(), reason="dataset missing")
def test_output_independent_of_input_row_order():
    import random
    from ghost.data import load_dataset, load_requests
    from ghost.intent import rule_intent
    from ghost.pipeline import Auditor

    def run(seed):
        ds = load_dataset()
        reqs, _ = load_requests(DATASET / "deletion_requests.csv")
        if seed is not None:
            rnd = random.Random(seed)
            rnd.shuffle(ds.activity)
            rnd.shuffle(ds.evidence_files)
            rnd.shuffle(reqs)
        res = Auditor(ds, lambda q: rule_intent(q.request_text)).audit(reqs)
        return {r.case_id: (r.status, json.dumps(r.steps, sort_keys=True)) for r in res}

    base = run(None)
    assert run(7) == base and run(1234) == base
