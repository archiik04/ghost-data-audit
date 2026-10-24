"""Evaluate the auditor on the labelled sample requests.

Usage (from any directory):
    python code/evaluation/main.py              # rules-only intent (offline, free)
    python code/evaluation/main.py --model      # LLM intent with majority voting
    python code/evaluation/main.py --show-diffs # print per-case missing/extra steps
Writes code/evaluation/out/sample_predictions.csv and metrics.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ghost.data import DATASET, load_dataset, load_requests  # noqa: E402
from ghost.intent import LLMIntentClassifier, Usage, make_client, rule_intent  # noqa: E402
from ghost.output import validate_output, write_output  # noqa: E402
from ghost.pipeline import Auditor  # noqa: E402
from metrics import compare, load_rows  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="use the LLM intent classifier")
    ap.add_argument("--show-diffs", action="store_true")
    ap.add_argument("--no-vision", action="store_true")
    args = ap.parse_args(argv)

    ds = load_dataset()
    samples, _ = load_requests(DATASET / "sample_deletion_requests.csv")
    pool, _ = load_requests(DATASET / "deletion_requests.csv")
    intent = (lambda r: rule_intent(r.request_text))
    usage = Usage()
    if args.model:
        client = make_client()
        intent = LLMIntentClassifier(client, cache_dir=HERE.parent / ".cache", usage=usage)
    vision = None
    if not args.no_vision:
        from ghost.vision import cached_vision
        vision = cached_vision(HERE.parent / ".cache", client=make_client() if args.model else None, usage=usage)
    if hasattr(intent, "prefetch"):
        intent.prefetch(samples)
    auditor = Auditor(ds, intent, vision=vision)
    sample_ids = {r.case_id for r in samples}
    results = auditor.audit(samples + pool, targets=sample_ids)

    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    pred_path = out_dir / "sample_predictions.csv"
    write_output(pred_path, [(r.case_id, r.status, r.steps) for r in results])
    known = set(ds_ids(ds))
    errors = validate_output(pred_path, sorted(sample_ids), known)
    gold = load_rows(DATASET / "sample_output.csv")
    report = compare(load_rows(pred_path), gold)
    summary = report["summary"]
    print(json.dumps(summary, indent=2))
    print("confusion (gold, pred):", dict(report["confusion"]))
    print("schema violations:", len(errors))
    print("model usage:", {"calls": usage.calls, "in": usage.input_tokens, "out": usage.output_tokens,
                           "cached": usage.cached_hits, "failures": usage.failures})
    for e in errors[:20]:
        print("  ", e)
    if args.show_diffs:
        by_id = {r.case_id: r for r in results}
        for c in report["per_case"]:
            if c["exact"]:
                continue
            print(f"\n{c['case_id']} gold={c['gold_status']} pred={c['pred_status']}")
            for m in c["missing"]:
                print("   - missing", m)
            for x in c["extra"]:
                print("   + extra  ", x)
            r = by_id[c["case_id"]]
            for rid, link in sorted(r.links.items()):
                print(f"     link {rid} {link.tier} {link.reasons[:2]}")
            for n in r.notes:
                print("     note", n)
    (out_dir / "metrics.json").write_text(json.dumps({"summary": summary, "per_case": report["per_case"],
                                                      "schema_violations": errors}, indent=2), encoding="utf-8")
    return 0


def ds_ids(ds):
    yield from (r["file_id"] for r in ds.evidence_files)
    yield from (r["activity_id"] for r in ds.activity)
    yield from (r["audit_id"] for r in ds.audit_log)


if __name__ == "__main__":
    sys.exit(main())
