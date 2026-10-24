"""Synthetic fixture worlds for regression and adversarial tests.

Every test builds a tiny, self-contained dataset in a temp directory; nothing here
reads or modifies the official dataset or reference answers (except the policy
file, which is copied as the authoritative rule set).
"""
from __future__ import annotations

import csv
import hashlib
import shutil
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from ghost.data import DATASET, load_dataset, load_requests  # noqa: E402
from ghost.intent import rule_intent  # noqa: E402
from ghost.pipeline import Auditor  # noqa: E402


def sha(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()


def _write_csv(path: Path, header: list, rows: list):
    with path.open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(header)
        w.writerows(rows)


class World:
    def __init__(self, root: Path):
        self.root = root
        self.identities = []
        self.audit_log = []
        self.activity = []
        self.evidence = []
        self.requests = []

    def person(self, uid, name, email, phone="+1 202 555 0100"):
        self.identities.append([uid, name, email, phone, sha(email), "2026-01-01T00:00:00Z"])
        return self

    def email_change(self, aid, uid, old, new, when="2026-02-01T00:00:00Z"):
        self.audit_log.append([aid, uid, when, "email", old, new])
        self.audit_log.append([aid + "h", uid, when, "email_hash", sha(old), sha(new)])
        return self

    def act(self, aid, when, etype, target, rid, result="SUCCESS", details="", source="deletion_worker"):
        self.activity.append([aid, when, etype, source, target, rid, result, details or f"Policy action completed for {rid}."])
        return self

    def file(self, fid, rel, captured, content: str):
        self.evidence.append((fid, rel, captured, content))
        return self

    def request(self, cid, email, text="Please delete my account and all my personal data.",
                requested="2026-09-01T12:00:00Z", status="COMPLETE", reported="2026-09-02T12:00:00Z",
                audit="2026-09-15T12:00:00Z"):
        self.requests.append([cid, email, requested, status, reported, audit, text])
        return self

    def build(self):
        r = self.root
        (r / "evidence").mkdir(parents=True, exist_ok=True)
        _write_csv(r / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
                   self.identities)
        _write_csv(r / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value",
                                              "new_value"], self.audit_log)
        _write_csv(r / "system_activity.csv", ["activity_id", "event_time", "event_type", "source_system",
                                               "target_system", "resource_id", "reported_result", "details"],
                   self.activity)
        shutil.copy(DATASET / "deletion_rules.csv", r / "deletion_rules.csv")
        rows = []
        for fid, rel, captured, content in self.evidence:
            p = r / "evidence" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            elif content is not None:
                p.write_text(content, encoding="utf-8")
            rows.append([fid, f"evidence/{rel}", "test_origin", captured])
        _write_csv(r / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], rows)
        _write_csv(r / "requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at",
                                        "audit_at", "request_text"], self.requests)
        return self

    def run(self, intent=None):
        self.build()
        ds = load_dataset(self.root)
        reqs, _ = load_requests(self.root / "requests.csv")
        auditor = Auditor(ds, intent or (lambda q: rule_intent(q.request_text)))
        return {res.case_id: res for res in auditor.audit(reqs)}, auditor


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "world")


def ops(result):
    return [(s["operation"], s["resource_id"], s["evidence_id"]) for s in result.steps]
