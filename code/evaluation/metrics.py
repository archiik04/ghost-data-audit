"""Supplementary evaluation metrics (the official scorer is hidden).

All metrics compare predicted rows with labelled rows for the same case_id.
"""
from __future__ import annotations

import json
from collections import Counter

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def step_key(step: dict) -> tuple:
    return tuple(step.get(f, "") for f in FIELDS)


def lcs(a: list, b: list) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def compare(pred: dict, gold: dict) -> dict:
    """pred/gold: case_id -> (status, steps list)."""
    cases = sorted(gold)
    out = {"cases": len(cases), "status_correct": 0, "exact_rows": 0, "sequence_exact": 0, "lcs_sum": 0.0,
           "tp": 0, "fp": 0, "fn": 0, "field_hits": Counter(), "field_total": 0, "false_complete": 0,
           "false_incomplete": 0, "per_case": [], "confusion": Counter()}
    for cid in cases:
        gs, gsteps = gold[cid]
        ps, psteps = pred.get(cid, ("MISSING", []))
        out["confusion"][(gs, ps)] += 1
        if ps == gs:
            out["status_correct"] += 1
        if ps == "COMPLETE" and gs != "COMPLETE":
            out["false_complete"] += 1
        if ps == "INCOMPLETE" and gs == "COMPLETE":
            out["false_incomplete"] += 1
        gk, pk = [step_key(s) for s in gsteps], [step_key(s) for s in psteps]
        if ps == gs and gk == pk:
            out["exact_rows"] += 1
        if gk == pk:
            out["sequence_exact"] += 1
        denom = max(len(gk), len(pk))
        out["lcs_sum"] += (lcs(gk, pk) / denom) if denom else 1.0
        gc, pc = Counter(gk), Counter(pk)
        tp = sum((gc & pc).values())
        out["tp"] += tp
        out["fp"] += sum(pc.values()) - tp
        out["fn"] += sum(gc.values()) - tp
        # field-level: align gold steps to predicted steps on (system_id, resource_id, phase)
        pmap = {}
        for s in psteps:
            pmap.setdefault((s.get("phase"), s.get("system_id"), s.get("resource_id")), s)
        for s in gsteps:
            out["field_total"] += 1
            p = pmap.get((s.get("phase"), s.get("system_id"), s.get("resource_id")))
            for f in FIELDS:
                if p is not None and p.get(f) == s.get(f):
                    out["field_hits"][f] += 1
        out["per_case"].append({"case_id": cid, "gold_status": gs, "pred_status": ps, "exact": ps == gs and gk == pk,
                                "missing": [list(k) for k in (gc - pc).elements()],
                                "extra": [list(k) for k in (pc - gc).elements()]})
    n = max(out["cases"], 1)
    p = out["tp"] / max(out["tp"] + out["fp"], 1)
    r = out["tp"] / max(out["tp"] + out["fn"], 1)
    out["summary"] = {
        "status_accuracy": out["status_correct"] / n,
        "exact_row_accuracy": out["exact_rows"] / n,
        "sequence_exact_accuracy": out["sequence_exact"] / n,
        "mean_sequence_similarity_lcs": out["lcs_sum"] / n,
        "step_precision": p, "step_recall": r, "step_f1": (2 * p * r / (p + r)) if p + r else 0.0,
        "field_accuracy": {f: out["field_hits"][f] / max(out["field_total"], 1) for f in FIELDS},
        "false_complete": out["false_complete"], "false_incomplete": out["false_incomplete"],
    }
    return out


def load_rows(path) -> dict:
    import csv
    rows = {}
    with open(path, encoding="utf-8", newline="") as h:
        for row in csv.DictReader(h):
            rows[row["case_id"]] = (row["deletion_status"], json.loads(row["steps_json"]))
    return rows
