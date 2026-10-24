"""Ghost Data deletion-audit agent - produce output.csv for dataset/deletion_requests.csv.

Usage (from any directory):
    python code/main.py                   # LLM intent + vision when OPENAI_API_KEY is set, else offline rules
    python code/main.py --offline         # never call a model (rules intent, cached vision only)
    python code/main.py --output path.csv # alternative output location
Writes <repo>/output.csv, validates it by reading it back, and writes
code/evaluation/out/run_report.json with usage, parse issues and per-case notes.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ghost.data import DATASET, REPO_ROOT, InputError, load_dataset, load_requests  # noqa: E402
from ghost.intent import LLMIntentClassifier, Usage, make_client, rule_intent  # noqa: E402
from ghost.output import validate_output, write_output  # noqa: E402
from ghost.pipeline import Auditor  # noqa: E402
from ghost.vision import cached_vision  # noqa: E402

log = logging.getLogger("ghost")


def run(output: Path, offline: bool = False, requests_file: Path | None = None) -> int:
    started = time.time()
    try:
        ds = load_dataset()
        requests, warnings = load_requests(requests_file or DATASET / "deletion_requests.csv")
    except InputError as exc:
        log.error("input error: %s", exc)
        return 2
    for w in warnings:
        log.warning(w)
    # Other participant-facing requests compete for workflow-only resources; include them in linking.
    pool = []
    sample_file = DATASET / "sample_deletion_requests.csv"
    if sample_file.exists() and (requests_file is None or Path(requests_file).resolve() != sample_file.resolve()):
        pool, _ = load_requests(sample_file)

    client = None if offline else make_client()
    usage = Usage()
    cache = HERE / ".cache"
    if client is None:
        log.info("no model client (offline or no OPENAI_API_KEY): rule-based intent, cached vision only")
        intent = (lambda r: rule_intent(r.request_text))
    else:
        intent = LLMIntentClassifier(client, cache_dir=cache, usage=usage)
    vision = cached_vision(cache, client=client, usage=usage)

    if hasattr(intent, "prefetch"):
        intent.prefetch(requests)
    auditor = Auditor(ds, intent, vision=vision)
    for issue in auditor.index.issues:
        log.warning("evidence issue %s %s: %s", issue.evidence_id, issue.category, issue.detail)
    ids = {r.case_id for r in requests}
    results = auditor.audit(requests + [p for p in pool if p.case_id not in ids], targets=ids)

    write_output(output, [(r.case_id, r.status, r.steps) for r in results])
    known = {r["file_id"] for r in ds.evidence_files} | {r["activity_id"] for r in ds.activity} \
        | {r["audit_id"] for r in ds.audit_log}
    errors = validate_output(output, [r.case_id for r in requests], known)
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    report = {
        "output": str(output), "requests": len(requests), "rows": len(results), "status_counts": counts,
        "schema_violations": errors, "runtime_seconds": round(time.time() - started, 1),
        "model_usage": {"calls": usage.calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "cached_hits": usage.cached_hits, "failures": usage.failures, "by_model": usage.by_model},
        "evidence_issues": [vars(i) for i in auditor.index.issues],
        "cases": [{"case_id": r.case_id, "status": r.status, "subject": r.subject,
                   "intent": vars(r.intent) if r.intent else None,
                   "links": {rid: {"tier": l.tier, "reasons": l.reasons, "conflicts": l.conflicts}
                             for rid, l in sorted(r.links.items())},
                   "notes": r.notes} for r in results],
    }
    out_dir = HERE / "evaluation" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s: %d rows %s; schema violations: %d", output, len(results), counts, len(errors))
    for e in errors[:20]:
        log.error("schema: %s", e)
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    ap.add_argument("--requests", type=Path, default=None, help="requests CSV (default dataset/deletion_requests.csv)")
    ap.add_argument("--offline", action="store_true", help="do not call any model API")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(args.output, offline=args.offline, requests_file=args.requests)


if __name__ == "__main__":
    sys.exit(main())
