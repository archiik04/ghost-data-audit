# Edge-case coverage matrix

Each row: failure mode → component → fixture → invariant → regression test → result.
Results are from the last run of `python -m pytest code/tests -q` (54 passed) unless the
row says otherwise. "Design" means the behaviour is guaranteed by construction and exercised
by the full-dataset run, without a dedicated unit test. "Not handled" rows are known limits.

## Identity and identifiers

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| I1 | Upper/lower case, whitespace in emails | `model.norm_email`, `IdentityGraph.resolve` | request `  Ana@X.com `, record `ANA@X.COM` | normalised exact match | `test_I1_email_case_and_whitespace_normalized` | pass |
| I2 | Historical email / historical hash | `IdentityGraph` (audit log + SHA-256 of every email) | old email in audit log, log keyed by its hash | linked as CONFIRMED | `test_I2_historical_email_and_hash_link` | pass |
| I3 | Same name, different person | `link_resources` (names never link) | two "Ana Lima" users | other person's record not linked | `test_I3_same_name_different_person_never_linked` | pass |
| I4 | Similar names / accents (decoys such as "Mateo Rios Gil") | names only used for exposure, never ownership | real dataset decoys (evidence_54, _57) | decoy rows never linked | design + sample eval (case_01/02/06 exact) | pass |
| I5 | Shared phone number between people | `link_resources` phone rule | two users share one phone | phone links only if unique to subject and no stronger identifier names someone else | `test_I3`, `test_I4`, `test_T4` (shared default phone) | pass (bug found and fixed by these tests) |
| I6 | Valid ID that belongs to a different person | `owners_of` + global assignment | workflow target owned by user_3 | never assigned by timing | `test_I4_foreign_owned_workflow_target_not_assigned_by_timing` | pass |
| I7 | Fabricated identifiers not in the corpus | `IdentityGraph.owner_index` | record for `user_999` | ignored | `test_A3_fabricated_identifier_is_ignored` | pass |
| I8 | Document naming several people | `document_mentions` single-subject rule | text with two emails | no ownership established | `test_I5_multi_subject_document_establishes_no_ownership` | pass |
| I9 | Alias that looks like an identity (e.g. "(user_17)") | catalog alias filter | real LINK activity `tombstone_17 (user_17)` | aliases never identity-like | design; fixed regression from sample case_08 | pass |
| I10 | Unknown requester email | `pipeline.audit` | email not in identities | no crash, note recorded | `test_I6_unknown_requester_does_not_crash` | pass |
| I11 | Resources with no identifiers at all (anonymised analytics, purged accounts) | `_assign_workflow` | real samples case_21/25-28 | assigned one-to-one by anchored evidence, status consistency, self-calibrated timing; tier SUPPORTED/CANDIDATE, never CONFIRMED | sample eval | partial: genuinely tied pairs (same reported_at, no identifiers) can swap (case_03/25-28) |
| I12 | Multiple simultaneous requests | global assignment over all participant requests | 6 requests reported 2026-09-05T12:00 | one resource → one request; one record per system per request | sample eval | partial (see I11) |
| I14 | Catalog claim contradicted by the record (screen shows another person under the claimed id) | CONTRADICTED tier in `link_resources` | catalog links `marketing_1` to Ana; screen shows another person | not planned, conflict reported | `test_V2_screen_showing_another_person_refutes_catalog_claim` | pass (real case: eval case_63) |
| I15 | Owner given as `lookup_value` in ledger rows | `ledger_to_obs` | bundle ledger row keyed by lookup_value | owner recognised, never reassigned by timing | `test_I15_ledger_lookup_value_establishes_owner` | pass (found by eval spot-check) |
| I16 | Non-account record linked by timing only | `_assign_workflow` anchor rule | anonymise event with no anchoring file | not linked | `test_I16_non_account_record_needs_anchor_for_timing_link` | pass |
| I13 | Recycled identifiers | `IdentityGraph` | checked on dataset: no old email is another user's current email | — | data check in development | not present in data; ambiguous identifiers resolve to `None` |

## Temporal and state

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| T1 | Evidence captured after `audit_at` | `reconstruct` (file capture time) | later dump contradicting earlier one | later file never used for state or citation | `test_T1_evidence_after_audit_never_changes_state` | pass |
| T2 | Deleted then recreated (sync/restore) | latest-capture-wins ordering | absent at T1, present at T2 | REPORT_REMAINING_RECORD citing T2 | `test_T2_recreation_after_deletion_latest_capture_wins` | pass |
| T3 | Job reports SUCCESS but data remains | activity treated as claims | DELETE SUCCESS + present snapshot | INCOMPLETE | `test_T3_success_claim_does_not_override_evidence`, `test_A2` | pass |
| T4 | Copy validly removed before audit | coverage absence in full dumps | present then absent | COMPLETE, cites the proving file | `test_T4_validly_removed_copy_gives_complete_with_evidence` | pass |
| T5 | Resource that did not exist yet (same person, earlier request) | `_first_seen` existence check | backup first seen 6 months after audit | excluded | `test_T5_resource_created_after_audit_is_out_of_scope` | pass (leak found on eval set, fixed) |
| T6 | Historical copy removed before the request | `_historical` | real sample case_17 (account_6523, tombstone_17) | out of scope when not touched by this workflow | sample eval | pass |
| T7 | Stale snapshot vs newer log | ordering by capture time, then in-record time | real evidence | newest capture wins | design | pass |
| T8 | Log export captured after audit but lines before it | ownership may use it, state may not | sample case_09 audit_record_4902 | linked; verified as future check with empty evidence | sample eval | pass |
| T9 | Time zones / missing timestamps | `parse_time` → aware UTC; missing row time falls back to capture time | — | no naive datetimes | design | pass |
| T11 | Retained record last seen before the request | history rule exempts security/legal | audit record seen a month earlier | still verified present | `test_T11_retained_record_seen_before_request_is_still_verified` | pass |
| T12 | Row dated after audit_at inside an earlier capture | row times never clamped; `reconstruct` drops rows with time > audit_at | manifest captured at audit time with a row dated two days later | future row ignored | `test_T12_row_dated_after_audit_inside_earlier_capture_is_ignored` | pass (real cases: eval case_135, case_383) |
| T10 | Replica lag / failover | presence in any replica table counts as present | primary empty, replica has row | REPORT_REMAINING_RECORD | `test_S1` | pass |

## Storage and evidence

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| S1 | Absent in primary, present in replica | `parse_sql` + `reconstruct` | replica-only row | remaining record | `test_S1_absent_in_primary_present_in_replica_is_remaining` | pass |
| S2 | Partial inventory extract mistaken for a full dump | coverage only from replica-topology SQL | empty inventory scope | absence not inferred | `test_S2_partial_inventory_absence_is_not_proof` | pass |
| S3 | Partial JSON snapshot bundles | bundle coverage only fills gaps | real evidence_51 | explicit 404/200 beats omission | sample eval (case_21/24) | pass |
| S4 | Missing, empty, corrupted files | `build_index` → `ParseIssue` | missing file, broken SQL, broken JSON | recorded, never silently skipped, run continues | `test_S3_missing_and_corrupt_files_are_reported_not_skipped` | pass |
| S5 | Unsupported formats | `ParseIssue(unsupported)` | — | listed in `run_report.json` | design | pass |
| S12 | Multi-person screen/table (several customers in one screenshot) | `resource_segments` row ownership | connector table with two customers | identifiers count only within the resource's own row, and only if that row names one person | `test_V1`, `test_I5` | pass |
| S13 | Process state shown only on a screen | `screen_state` | connector "Healthy · Continuous"; archive in a recovery console | active flow / restore risk reported | `test_V1` | pass (real cases: eval case_39, case_55) |
| S14 | Tombstone shown by its stable user_id in a reference registry | user-id alias for tombstones, single-person screens only | "user_1 · Retained reference" | VERIFY_RECORD_REDACTED citing the screen | `test_V3_retained_reference_screen_verifies_clean_tombstone` | pass |
| S15 | Ticket screenshot vs attachment | attachment type only for PDFs | support conversation screenshot | REDACT (shared context), not DELETE | eval diff (case_53) | pass |
| S16 | RFC 5424 syslog header carries the trace id | `SYSLOG_RE` in `parse_log_line` | `<15>1 node-9 account_service tr-1 - - …` | trace joins work in syslog archives | `test_S16`, `test_S16b` | pass (found by eval spot-check) |
| S17 | Inventory scope as evidence of absence | `sql_inventory` coverage, fallback only | empty account scope, no explicit sighting | VERIFY_RECORD_ABSENT citing the inventory | `test_S17_inventory_scope_proves_absence_only_when_nothing_explicit` | pass |
| S18 | Input row order | deterministic sorting everywhere | shuffled activity, manifest, requests | identical output | `test_output_independent_of_input_row_order` | pass |
| S19 | Citation captured after audit_at | `reconstruct` availability filter | real output.csv | none | `test_no_citation_postdates_audit_at` | pass |
| S6 | Images without a model | vision returns None → `ParseIssue(model_error/unsupported)` | offline run | reported; cached transcriptions reused | design | pass |
| S7 | Identifiers in unexpected fields (`subject_selector`, `lookup_value`, `uploaded_by`) | field vocabulary in `evidence.py` | real dataset field scan | all identifier-bearing fields mapped | field-scan script during development | pass |
| S8 | Free-text PII inside messages | `identifiers_in_text` on message fields | real support messages | exposure detected | design | pass |
| S9 | Four log syntaxes (kv, JSON, bracket, pipe) + nested `message=` | `parse_log_line` | real logs | trace_id joins lookup key to resource | sample eval | pass |
| S10 | CSV/JSONL/XML/HTML evidence | `.csv/.txt/.md` via text parser, `.jsonl` supported | — | — | — | XML/HTML not present in corpus; would be reported as unsupported |
| S11 | Scanned PDFs / poor OCR | PyMuPDF text layer (pypdf fallback) | — | — | — | not handled: no OCR for image-only PDFs (none in corpus) |

## Policy and retention

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| P1 | Retained audit record must stay available | RETAIN → VERIFY_RECORD_PRESENT | audit record in retention | never deleted | `test_P1_...`, `test_A4_retained_record_with_identifiers_still_retained` | pass |
| P2 | Expired retention exception | `plan_resource` | retention_until < audit_at | treated as deletable | `test_P1_retained_audit_record_verified_present_expired_one_deleted` | pass |
| P3 | Tombstone with PII | ACCOUNT_TOMBSTONE → REDACT | tombstone with name/email | REPORT_EXPOSED_PII + REDACT | `test_P2_tombstone_keeps_reference_but_not_identifiers` | pass |
| P4 | Clean tombstone | user_id reference allowed | tombstone with only user_id | VERIFY_RECORD_REDACTED with evidence | `test_P3_clean_tombstone_verified_redacted` | pass |
| P5 | Analytics must be anonymised, not deleted | ANALYTICS_PROFILE → ANONYMIZE | identifiable analytics record | ANONYMIZE_RECORD | `test_P5_analytics_identifiable_is_anonymized_not_deleted` | pass |
| P6 | Support message with shared context vs private message vs attachment | `Policy.decide` | real samples (case_07, 10, 13, 19) | REDACT shared; DELETE private/attachment | sample eval | pass |
| P7 | Backup within retention but restorable | BACKUP_ARCHIVE → keep, BLOCK_RESTORE | restore_allowed=true | REPORT_RESTORE_RISK + BLOCK_RESTORE | `test_P4_flows_and_restores_handled_before_records` | pass |
| P8 | Several rules for one category | most protective wins | — | retain > redact/anonymize > delete | design | pass |
| P9 | No rule for a category | personal data defaults to DELETE, noted | — | — | design | pass |

## Resurrection and synchronisation

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| R1 | Active sync / connector | DATA_FLOW → DISABLE first | heartbeat enabled=true | REPORT_ACTIVE_DATA_FLOW, DISABLE before record changes | `test_A5_hidden_active_flow_makes_case_incomplete`, `test_P4` | pass |
| R2 | Disabled flow | VERIFY_DATA_FLOW_DISABLED (with evidence when COMPLETE) | enabled=false | no finding | sample eval (case_26-28) | pass |
| R3 | Restore path without deletion block | restore_candidate eligible & !deletion_block | real logs | REPORT_RESTORE_RISK | sample eval (case_09, 14, 17) | pass |
| R4 | Historical sync event no longer active | latest state wins | — | only current state reported | design | pass |
| R5 | Fixed-but-unverified path | remediation steps always followed by verification | — | verify step per remediated item | `test_O3` | pass |

## Request classification

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| Q1 | Access/export, correction, opt-out requests | LLM schema + rules | export / correction / unsubscribe texts | NOT_APPLICABLE, `[]` | `test_R1_non_deletion_request_is_not_applicable`, `test_M5`, `test_M6` | pass |
| Q2 | Multilingual deletion requests | LLM; rules cover FR/ES/DE/JA/ZH/KO/RU/... | real samples (ES, FR, JA) | deletion recognised | `test_M6_rule_intent_multilingual`, sample eval | pass |
| Q3 | "Verify my deletion" phrasing | LLM prompt counts verification of a deletion as deletion | real samples case_25/26 | DELETE intent | sample eval (rules alone failed these, LLM fixed) | pass |
| Q4 | Prompt injection in request text | quoted as untrusted; label enum only | "Ignore previous instructions…" | cannot change anything but the label | `test_M3_request_text_is_quoted_as_untrusted` | pass |

## Output, execution and model reliability

| ID | Failure mode | Component | Fixture | Invariant | Test | Result |
|---|---|---|---|---|---|---|
| O1 | Commas, quotes, unicode in CSV | `write_output` (csv module) | quoted request text | round-trips | `test_O1`, `test_O4` | pass |
| O2 | Duplicate / missing rows, bad status, invalid JSON | `validate_output` | crafted bad CSV | all detected | `test_O2_validator_catches_contract_violations` | pass |
| O3 | Step ordering and numbering | `finalize` | shuffled plan | phase → operation table → system → resource; step_no 1..n; future evidence empty | `test_O3_finalize_orders_phases_and_blanks_future_evidence` | pass |
| O4 | Duplicate request rows, missing columns | `load_requests` | duplicate case_id; missing columns | first kept + warning; InputError | `test_O4_duplicate_requests_and_missing_columns` | pass |
| O5 | Interrupted run | atomic write (temp file + replace) | — | never a half-written output.csv | design | pass |
| M1 | Model disagreement | 3-vote majority, rules break ties | 2:1 vote | majority wins | `test_M1_majority_vote` | pass |
| M2 | Timeouts, malformed JSON, invalid labels | bounded retries → rule fallback | failing fake client | never a fabricated label | `test_M2_malformed_and_failing_model_falls_back_to_rules` | pass |
| M3 | Corrupted cache entry | cache read guarded, entry recomputed | corrupt file | recomputed | `test_M4_corrupt_cache_entry_is_recomputed` | pass |
| M4 | Missing API key / offline | `make_client` → None | no key | rules + cached vision | `test_M5_offline_mode_uses_rules`, `--offline` run | pass |
| M5 | Stale cache after config change | cache key = model + prompt version + content hash | — | changed model/prompt invalidates | design | pass |
| X1 | Different working directory / Windows paths | paths from `__file__`, `pathlib` | runs from `C:\Users\HP` | identical output | manual runs | pass |
| X2 | Secrets in package | `tools/package.py` scan + exclusions | — | fails on secret-like strings, dataset files, caches | package run | pass |
