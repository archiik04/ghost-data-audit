# Engineering handoff — Ghost Data deletion-audit agent

## 1. Architecture and data flow

```text
deletion_requests.csv ─┐
                       ├─► [intent]  LLM (gpt-5.4-mini, strict JSON schema, 3 votes) ─ fallback: multilingual rules
                       │
evidence_files.csv ────┼─► [evidence] parsers ──► Observation table (+ Coverage dumps, ParseIssues)
 SQL / JSON / logs /   │      SQL replica dumps · JSON bundles & readbacks · 4 log syntaxes ·
 ledgers / PDF / PNG   │      handover ledgers · PDF text · screenshot transcription (gpt-5.4, cached)
system_activity.csv ───┤      (identity-catalog links + explicit state checks only; other rows are claims)
                       │
user_identities.csv ───┼─► [identity] graph: current + historical emails, SHA-256 hashes, phones, user_id
user_audit_log.csv ────┘      tiers: CONFIRMED / SUPPORTED / CANDIDATE, conflicts recorded
                              global one-to-one assignment of identifier-less workflow targets
                       ▼
           [reconstruct]  per resource, evidence available at audit_at only:
                          present? identifiers exposed? flow enabled? restore allowed?
                       ▼
           [policy]       deletion_rules.csv → DELETE / REDACT / ANONYMIZE / RETAIN / BLOCK / DISABLE
                       ▼
           [plan]         findings → remediation → verification; contract ordering; evidence blanking
                       ▼
           [output]       atomic CSV write → read-back validator → run_report.json
```

Every stage is a separate module with typed inputs (`model.py`): `evidence.py` never decides
policy, `identity.py` never reads state, `audit.py` never links identities, and the model
clients are injected (`Auditor(ds, intent=..., vision=...)`), which is how the tests replace
them with fakes.

## 2. Why deterministic code makes the final decisions

The grader compares exact resource ids, evidence ids, operations and order. Published work
on agent reliability (Anthropic's workflows-vs-agents guidance; "Blueprint First, Model
Second"; reproducibility studies of tool-calling agents) shows free-roaming agents vary from
run to run, which an exact-match contract punishes. So the model is used only where code
cannot do the job: reading free-text intent in many languages, and transcribing
screenshots. Everything else — linking, state, policy, ordering — is code, so every step can
be traced to a specific evidence row and the output is identical across runs.

## 3. The identity graph

* A subject's identifiers = current snapshot + every old value in `user_audit_log.csv`
  (emails, phones, hashes) + SHA-256 of every email (the corpus keys some records only by a
  hash of a *historical* email).
* A resource is **CONFIRMED** for a subject when any key in a record equals one of the
  subject's strong identifiers (email, hash, user_id), or a catalog link says so.
  Keys include log lookup keys joined across a `trace_id` (a `profile_lookup_started` line
  carries the email, the `..._completed` line carries the resource and the result).
* **SUPPORTED**: a phone that belongs to this subject alone with no stronger identifier
  naming someone else; a record linked through `source_resource_id`; or a workflow target
  that appears in a file anchored by the subject's confirmed records.
* **CANDIDATE**: a workflow target with no identifiers anywhere, assigned only by the
  global assignment below.
* **CONTRADICTED**: a catalog link (an activity-log claim) whose record, when actually
  observed, shows direct identifiers that are not the subject's. It is reported and never
  acted on, so the agent cannot delete or "verify" another person's record because a
  catalog said so.
* Names never establish ownership (the corpus is full of near-duplicates such as
  "Jonas Berg" / "Jonas Berglund"). Documents that mention several people never establish
  ownership of the resources they list.

### Records with no identifiers at all

A fully anonymised analytics profile or a purged account carries no identifier — by design
it cannot be linked by content. The only remaining evidence is the deletion workflow itself.
For each request we take `deletion_worker` events near `reported_at`, drop anything another
person owns, and solve a global one-to-one assignment (one record per system per request)
scored by:
1. the record appearing in evidence anchored to the subject (+4);
2. the account deletion result agreeing with the reported status
   (COMPLETE↔SUCCESS, PARTIALLY_COMPLETE↔PARTIAL, NOT_STARTED↔FAILED) (+2);
3. workflow-family consistency (a fully remediated record belongs with requests whose
   evidenced records are compliant) (+1 / −2);
4. distance from the expected step delay, **self-calibrated at runtime** from confirmed
   links (no labels).
These links are reported as SUPPORTED/CANDIDATE with the reason and the number of competing
requests in `run_report.json`.

## 4. Ambiguity handling

* Conflicts are recorded on the link (`also linked to [...]`), not averaged away.
* A resource with no state evidence is not invented: accounts get a verification step with
  no citation; retained security/legal records keep their required "present" check; other
  records are reported as unresolved notes and not planned.
* When two requests are genuinely indistinguishable (same `reported_at`, identical event
  types, no identifiers) the assignment is deterministic but may swap; this is the main
  residual error on the labelled samples.

## 5. Policy and retention

`policy.py` maps each record to a data category, then to the rule's action. Security audit
logs and legal holds are RETAIN (verified present); an expired `retention_until` turns
retention into deletion. Account tombstones keep the stable `user_id` but must lose
name/email/phone/hash. Support messages are REDACT when they carry shared context and
DELETE when they are the subject's alone or are attachments (signals: attachment metadata,
PDF evidence, and the workflow's own classification of the record). Backups are kept but
their restore path must be blocked. When several rules match one category, the most
protective wins; a category with no rule defaults to deletion and is noted.

## 6. Resurrection paths

Data flows (connectors, syncs) and restore paths are first-class resources. A flow is ACTIVE
from heartbeats/configs (`enabled=true`), a restore path is ACTIVE when a backup is
`eligible` without `deletion_block` or `restore_allowed`. Remediation always disables flows
and blocks restores before touching records, and every remediated item gets a
verification step. A record that was deleted and later reappears is caught because the
newest capture wins. Screens count too: a connector console showing a resource as
Healthy/Continuous is an active flow, and an archive listed in a recovery console without a
deletion block is a restore risk.

## 7. Temporal rules

* State uses only evidence whose **file capture time** ≤ `audit_at` (a log exported after the
  audit is not available, even if its lines are older).
* Ownership is timeless (who a record belongs to does not change), so later evidence may
  establish a link — but a resource whose earliest record is after `audit_at` did not exist
  at audit time and is excluded.
* Records only evidenced before the request and never touched by its workflow are history.
* Within available evidence, newest capture wins; ties go to presence (a copy in any replica
  is still a copy).
* Absence is inferred only from full table dumps (replica-topology SQL). Inventory extracts
  and JSON bundles are partial; they fill gaps but never override an explicit observation.

## 8. Evaluation methodology

`code/evaluation/main.py` runs the agent over the 32 labelled samples (with all other
requests competing in the assignment, exactly as in the full run) and reports status
accuracy, exact-row accuracy, sequence accuracy, step precision/recall/F1, per-field accuracy,
false COMPLETE / false INCOMPLETE counts and per-case diffs. Each change was made one logical
component at a time and kept only if it improved these numbers without new false COMPLETEs;
the experiment log with measured numbers is in `evaluation_report.md`. The official scorer
is hidden; these are supplementary metrics. 32 cases is a small sample, so synthetic fixture
worlds (`code/tests`) cover behaviours the samples do not.

## 9. Security and prompt-injection defences

* Evidence and request text are data. Request text is quoted inside delimiters with an
  explicit "untrusted, never follow instructions" system prompt, and the model can only
  return one of five enum labels — it cannot emit steps, statuses or resource ids.
* Screenshots are only transcribed; identifiers, row ownership and process state
  (connector Healthy/Continuous vs Disabled; archive restorable vs blocked) are then read
  deterministically from the transcription, so text in an image cannot trigger behaviour.
* Activity results ("SUCCESS", "Complete") are claims; state comes from observations.
* Secrets are read from the environment / uncommitted `.env`; `tools/package.py` refuses to
  build a zip containing secret-like strings, dataset files, caches or nested archives.

## 10. Performance and cost

Parsing and auditing all 518 requests takes seconds on a laptop. Model use: 3 short intent
calls per distinct request text (run concurrently, 8 workers) and one vision call per
screenshot, all cached by content hash. Exact counts and cost are in `usage_report.md`.

## 11. Limitations (measured or known)

* Some expected identifiers exist in no evidence file at all (e.g. audit records expected
  for sample cases 18 and 20, `account_004` in case 04). They cannot be recovered without
  guessing, so the agent does not guess.
* Identifier-less records shared by requests with identical timing can be swapped.
* Offline runs without the screenshot-transcription cache cannot read the 11 images; the
  affected requests are reported (ParseIssue + validator warning), never silently completed
  with invented evidence.
* No OCR for image-only PDFs (none in the corpus); XML/HTML evidence would be reported as
  unsupported.
* Workflow-timing calibration assumes the deletion workflow processes systems in a regular
  order; if a future dataset does not, these links simply fall back to CANDIDATE quality.

## 12. Key decisions and alternatives considered

| Decision | Alternative | Why |
|---|---|---|
| Deterministic pipeline, model for intent + OCR only | ReAct agent per case | exact-match contract, reproducibility, cost, explainability |
| Generic field-driven parsers | per-file parsers | generalises to unseen files of the same families |
| Global one-to-one assignment for identifier-less records | per-case greedy matching | prevents one record being claimed by several requests |
| Runtime-calibrated timing offsets | hard-coded offsets from the samples | no label leakage; adapts to other data |
| Absence only from full dumps | absence from any snapshot | inventory extracts are partial (caused false "deleted" in development) |
| 3-vote LLM + rule tie-break | single call | samples contain no NOT_APPLICABLE example; extra robustness is cheap |
