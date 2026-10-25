"""Print everything needed to audit one case by hand.

    python code/tools/inspect_case.py case_123 [case_456 ...]
Shows the request, the subject's identifiers, the predicted steps, the identity links with
tiers and reasons, every raw evidence line that mentions the subject or a linked resource
(file capture time marked if after audit_at), and the activity rows for linked resources.
Uses only participant-facing data plus code/evaluation/out/run_report.json.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost.data import DATASET, load_dataset  # noqa: E402
from ghost.identity import IdentityGraph  # noqa: E402
from ghost.model import parse_time  # noqa: E402


def main(case_ids):
    ds = load_dataset()
    graph = IdentityGraph(ds.identities, ds.audit_log)
    reqs = {r["case_id"]: r for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))}
    reqs.update({r["case_id"]: r for r in csv.DictReader(open(DATASET / "sample_deletion_requests.csv",
                                                                 encoding="utf-8"))})
    out = {r["case_id"]: r for r in csv.DictReader(open(ROOT / "output.csv", encoding="utf-8"))}
    report = ROOT / "code" / "evaluation" / "out" / "run_report.json"
    cases = {c["case_id"]: c for c in json.loads(report.read_text(encoding="utf-8"))["cases"]} if report.exists() else {}
    for cid in case_ids:
        q = reqs[cid]
        audit = parse_time(q["audit_at"])
        subj = graph.resolve(q["email"])
        print("=" * 100)
        print(cid, q["email"], "req", q["requested_at"], q["reported_status"], "rep", q["reported_at"], "audit", q["audit_at"])
        print("  text:", q["request_text"][:160])
        terms = set()
        if subj:
            terms = set(subj.emails) | set(subj.hashes) | {subj.user_id}
            print("  subject", subj.user_id, sorted(subj.emails), "phones", sorted(subj.phones))
        if cid in out:
            print("  OUTPUT", out[cid]["deletion_status"])
            for s in json.loads(out[cid]["steps_json"]):
                print("    ", s["step_no"], s["operation"], s["system_id"], s["resource_id"], s["evidence_id"])
        c = cases.get(cid, {})
        for rid, l in c.get("links", {}).items():
            print("  link", rid, l["tier"], l["reasons"][:2], l.get("conflicts", []))
            terms.add(rid)
        for n in c.get("notes", []):
            print("  note", n)
        for row in ds.evidence_files:
            path = ds.root / row["file_path"]
            if path.suffix.lower() in (".png", ".pdf") or not path.exists():
                continue
            late = parse_time(row["captured_at"]) > audit
            hits = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if any(t in ln.lower() or t in ln for t in terms)]
            if hits:
                print(f"  -- {row['file_id']} {row['file_path']} captured {row['captured_at']}{'  (AFTER AUDIT)' if late else ''}")
                for h in hits[:12]:
                    print("       ", h[:230])
        for a in ds.activity:
            if a["resource_id"] in terms:
                print("  act", a["activity_id"], a["event_time"], a["event_type"], a["reported_result"], a["resource_id"],
                      "|", a["details"][:90])


if __name__ == "__main__":
    main(sys.argv[1:])
