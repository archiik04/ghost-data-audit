# Ghost Data Still Exists — deletion-audit agent

Solution for HackerRank Orchestrate (October 2026). For every request in
`dataset/deletion_requests.csv` the agent decides whether deletion was actually
requested, reconstructs the subject's current and historical identifiers, finds every
copy of their data across the supplied evidence, rebuilds each record's state at
`audit_at`, applies `deletion_rules.csv`, and writes ordered findings, remediation
steps, and verification checks to `output.csv`.

Problem statement: [`problem_statement.md`](./problem_statement.md).
Architecture, decisions and interview notes: [`code/docs/HANDOFF.md`](./code/docs/HANDOFF.md).
Edge-case coverage matrix: [`code/docs/EDGE_CASES.md`](./code/docs/EDGE_CASES.md).
Measured results: [`code/evaluation/evaluation_report.md`](./code/evaluation/evaluation_report.md),
[`code/evaluation/usage_report.md`](./code/evaluation/usage_report.md).

## Setup

Python 3.10+ (tested on 3.11, Windows 11 and POSIX paths).

```bash
pip install -r code/requirements.txt
```

Model access is optional. To enable the LLM intent classifier and screenshot
transcription, put an OpenAI key in an uncommitted `.env` at the repository root
(see `.env.example`):

```text
OPENAI_API_KEY=...
```

Optional overrides: `GHOST_INTENT_MODEL` (default `gpt-5.4-mini`), `GHOST_VISION_MODEL`
(default `gpt-5.4`). Without a key the agent runs fully offline: rule-based intent and
any cached screenshot transcriptions (`code/.cache/`). Offline without that cache, the 11
screenshots are reported as unread evidence in `run_report.json`, a few requests whose only
evidence is a screenshot lose their checks (reported as validator warnings), and two
"please verify my deletion" requests in the samples are misread by the rules; the submitted
`output.csv` was produced with the models enabled.

## Run

All commands work from any working directory; paths resolve from the code location.

```bash
python code/main.py                 # writes ./output.csv (518 rows) and validates it
python code/main.py --offline       # same, never calls a model
python code/evaluation/main.py --model --show-diffs   # score against the 32 labelled samples
python -m pytest code/tests -q      # regression + adversarial suite
python code/evaluation/write_usage_report.py          # usage_report.md from the last full run
python code/tools/package.py        # build and inspect code.zip (no dataset, caches or secrets)
```

`code/main.py` exits non-zero if the written CSV fails the contract validator
(header, coverage of every case_id, ordering, JSON validity, operation/phase/state
consistency, evidence rules).

## Layout

```text
code/
  main.py                 entry point -> output.csv + evaluation/out/run_report.json
  ghost/
    model.py              typed observations, identifier normalisation
    data.py               CSV loading and validation
    evidence.py           parsers: SQL snapshots, JSON, logs (4 formats), ledgers, PDF, images
    identity.py           identity graph and evidence tiers (confirmed / supported / candidate)
    pipeline.py           orchestration, global workflow-target assignment
    audit.py              state reconstruction at audit_at, step planning and ordering
    policy.py             deletion_rules.csv -> required treatment
    intent.py             LLM intent classifier (3-vote, schema-validated, cached) + rule fallback
    vision.py             screenshot transcription (cached by image hash)
    output.py             serialisation and the final schema validator
  evaluation/             metrics, evaluation harness, reports
  tests/                  synthetic fixture worlds (no official data modified)
  tools/package.py        submission packaging with secret checks
  docs/                   handoff document and edge-case matrix
```
