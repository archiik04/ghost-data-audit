"""Regression tests: identity, temporal, storage, policy, ordering (edge-case matrix rows I*, T*, S*, P*, O*)."""
import json

from conftest import ops, sha


def sql_dump(primary: list, replica: list = (), topology="read_replica"):
    lines = [f"-- Database topology: {topology}", "BEGIN TRANSACTION;"]
    for table, rows in (("customer_data_primary", primary), ("customer_data_read_replica", replica)):
        lines.append(f"CREATE TABLE {table} (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,"
                     " record_json TEXT, updated_at TEXT);")
        for rec in rows:
            system = rec.pop("_system")
            body = json.dumps(rec).replace("'", "''")
            lines.append(f"INSERT INTO {table} VALUES('{rec['resource_id']}','{system}','c','{body}',"
                         "'2026-09-01T00:00:00Z');")
    lines.append("COMMIT;")
    return "\n".join(lines)


def account(rid, uid, email, name="Ana Lima"):
    return {"_system": "account_service", "resource_id": rid, "customer_reference": uid, "full_name": name,
            "email": email, "phone": "+1 202 555 0100", "account_status": "ACTIVE"}


def base(world):
    return (world.person("user_1", "Ana Lima", "ana@x.com")
            .person("user_2", "Ana Lima", "ana.lima@y.com")  # same name, different person
            .person("user_3", "Bo Chen", "bo@x.com"))


# ---------------------------------------------------------------- identity (I*)

def test_I1_email_case_and_whitespace_normalized(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ANA@X.COM")]))
    world.request("c1", "  Ana@X.com ")
    res, _ = world.run()
    assert res["c1"].subject == "user_1"
    assert ("REPORT_REMAINING_RECORD", "account_1", "e1") in ops(res["c1"])


def test_I2_historical_email_and_hash_link(world):
    base(world).email_change("a1", "user_1", "ana.old@x.com", "ana@x.com")
    log = ("2026-09-10T10:00:00Z level=INFO service=analytics_platform trace_id=t1 resource_id=analytics_7 "
           f'event=contact_lookup_completed rows=1 correlation_key="{sha("ana.old@x.com")}"\n')
    world.file("e1", "l.log", "2026-09-10T11:00:00Z", log).request("c1", "ana.old@x.com")
    res, _ = world.run()
    assert res["c1"].subject == "user_1"
    assert res["c1"].links["analytics_7"].tier == "confirmed"


def test_I3_same_name_different_person_never_linked(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z",
                     sql_dump([account("account_2", "user_2", "ana.lima@y.com")]))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert "account_2" not in res["c1"].links


def test_I4_foreign_owned_workflow_target_not_assigned_by_timing(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_3", "user_3", "bo@x.com")]))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_3")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert "account_3" not in res["c1"].links


def test_I5_multi_subject_document_establishes_no_ownership(world):
    base(world).file("e1", "doc.txt", "2026-09-10T00:00:00Z",
                     "Register: support_9 mentions ana@x.com and bo@x.com\n")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert "support_9" not in res["c1"].links


def test_I6_unknown_requester_does_not_crash(world):
    base(world).request("c1", "nobody@nowhere.com")
    res, _ = world.run()
    assert res["c1"].subject is None
    assert any("does not resolve" in n for n in res["c1"].notes)


# ---------------------------------------------------------------- temporal (T*)

def test_T1_evidence_after_audit_never_changes_state(world):
    base(world)
    world.file("e1", "old.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.file("e2", "new.sql", "2026-09-20T00:00:00Z", sql_dump([account("account_9", "user_3", "bo@x.com")]))
    world.request("c1", "ana@x.com", audit="2026-09-15T12:00:00Z")
    res, _ = world.run()
    assert res["c1"].status == "INCOMPLETE"  # the later dump (absence) is not available at audit time
    assert ("REPORT_REMAINING_RECORD", "account_1", "e1") in ops(res["c1"])


def test_T2_recreation_after_deletion_latest_capture_wins(world):
    base(world)
    world.file("e1", "a.sql", "2026-09-05T00:00:00Z", sql_dump([account("account_8", "user_3", "bo@x.com")]))
    world.file("e2", "b.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_1")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    # absent in e1 but recreated in e2 -> still present at audit
    assert ("REPORT_REMAINING_RECORD", "account_1", "e2") in ops(res["c1"])


def test_T3_success_claim_does_not_override_evidence(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_1", "SUCCESS")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert res["c1"].status == "INCOMPLETE"


def test_T4_validly_removed_copy_gives_complete_with_evidence(world):
    base(world)
    world.file("e0", "a.sql", "2026-09-01T06:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.file("e1", "b.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_8", "user_3", "bo@x.com")]))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_1")
    world.request("c1", "ana@x.com", requested="2026-08-31T12:00:00Z")
    res, _ = world.run()
    assert res["c1"].status == "COMPLETE"
    assert ops(res["c1"]) == [("VERIFY_RECORD_ABSENT", "account_1", "e1")]


# ---------------------------------------------------------------- storage (S*)

def test_S1_absent_in_primary_present_in_replica_is_remaining(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z",
                     sql_dump([], [account("account_1", "user_1", "ana@x.com")]))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert ("REPORT_REMAINING_RECORD", "account_1", "e1") in ops(res["c1"])


def test_S2_partial_inventory_absence_is_not_proof(world):
    base(world).file("e1", "a.sql", "2026-09-05T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    inv = ("CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT);\n"
           "INSERT INTO snapshot_scope VALUES ('account_service','2026-09-10T00:00:00Z');\n"
           "CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, email TEXT);\n")
    world.file("e2", "inv.sql", "2026-09-10T00:00:00Z", inv).request("c1", "ana@x.com")
    res, _ = world.run()
    assert ("REPORT_REMAINING_RECORD", "account_1", "e1") in ops(res["c1"])


def test_S3_missing_and_corrupt_files_are_reported_not_skipped(world):
    base(world).file("e1", "gone.json", "2026-09-10T00:00:00Z", None)
    world.file("e2", "bad.sql", "2026-09-10T00:00:00Z", "CREATE TABLE (((;")
    world.file("e3", "bad.json", "2026-09-10T00:00:00Z", "{not json")
    world.request("c1", "ana@x.com")
    res, auditor = world.run()
    cats = {(i.evidence_id, i.category) for i in auditor.index.issues}
    assert ("e1", "missing_file") in cats and ("e2", "unreadable") in cats and ("e3", "unreadable") in cats
    assert "c1" in res


# ---------------------------------------------------------------- policy (P*)

def json_export(service, coll, rows):
    return json.dumps({"exported_at": "2026-09-10T00:00:00Z", "service_exports": {service: {coll: rows}}})


def test_P1_retained_audit_record_verified_present_expired_one_deleted(world):
    base(world).file("e1", "s.json", "2026-09-10T00:00:00Z", json_export("security_service", "audit_records", [
        {"resource_id": "audit_record_1", "subject_reference": "user_1", "retention_until": "2030-01-01T00:00:00Z"},
        {"resource_id": "audit_record_2", "subject_reference": "user_1", "retention_until": "2026-01-01T00:00:00Z"},
    ]))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    o = ops(res["c1"])
    assert ("VERIFY_RECORD_PRESENT", "audit_record_1", "") in o
    assert ("REPORT_REMAINING_RECORD", "audit_record_2", "e1") in o


def test_P2_tombstone_keeps_reference_but_not_identifiers(world):
    base(world).file("e1", "t.json", "2026-09-10T00:00:00Z", json.dumps({"snapshots": [
        {"captured_at": "2026-09-10T00:00:00Z", "data_store": "identity_service", "records": [
            {"resource_id": "tombstone_1", "subject_reference": "user_1", "name": "Ana Lima", "email": "ana@x.com",
             "phone": None, "email_hash": None, "account_status": "DELETED"}]}]}))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    o = ops(res["c1"])
    assert ("REPORT_EXPOSED_PII", "tombstone_1", "e1") in o and ("REDACT_RECORD", "tombstone_1", "") in o


def test_P3_clean_tombstone_verified_redacted(world):
    base(world).file("e1", "t.json", "2026-09-10T00:00:00Z", json.dumps({"snapshots": [
        {"captured_at": "2026-09-10T00:00:00Z", "data_store": "identity_service", "records": [
            {"resource_id": "tombstone_1", "subject_reference": "user_1", "name": None, "email": None,
             "phone": None, "email_hash": None, "account_status": "DELETED"}]}]}))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert res["c1"].status == "COMPLETE"
    assert ops(res["c1"]) == [("VERIFY_RECORD_REDACTED", "tombstone_1", "e1")]


def test_P4_flows_and_restores_handled_before_records(world):
    base(world).file("e1", "x.json", "2026-09-10T00:00:00Z", json.dumps({"service_exports": {
        "integration_service": {"connectors": [{"resource_id": "data_flow_1", "customer_reference": "user_1",
                                                "enabled": True, "observed_at": "2026-09-10T00:00:00Z"}]},
        "backup_service": {"restore_entries": [{"resource_id": "backup_1", "customer_email": "ana@x.com",
                                                "restore_allowed": True, "observed_at": "2026-09-10T00:00:00Z"}]},
        "search_service": {"index_queries": [{"resource_id": "search_1", "query": "ana@x.com",
                                              "observed_at": "2026-09-10T00:00:00Z",
                                              "hits": {"total": 1, "documents": [
                                                  {"resource_id": "search_1", "email": "ana@x.com"}]}}]},
    }}))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    rem = [s["operation"] for s in res["c1"].steps if s["phase"] == "REMEDIATION"]
    assert rem == ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD"]
    for s in res["c1"].steps:
        assert (s["evidence_id"] == "") == (s["phase"] != "FINDING")


def test_P5_analytics_identifiable_is_anonymized_not_deleted(world):
    base(world).file("e1", "x.json", "2026-09-10T00:00:00Z", json_export("analytics_platform", "profiles", [
        {"resource_id": "analytics_1", "email": "ana@x.com", "customer_reference": None,
         "observed_at": "2026-09-10T00:00:00Z"}]))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    o = ops(res["c1"])
    assert ("ANONYMIZE_RECORD", "analytics_1", "") in o and not any(op == "DELETE_RECORD" for op, *_ in o)


# ---------------------------------------------------------------- request classification (R*)

def test_R1_non_deletion_request_is_not_applicable(world):
    base(world).file("e1", "a.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.request("c1", "ana@x.com", text="Please send me a copy of all the data you hold about me.")
    res, _ = world.run()
    assert res["c1"].status == "NOT_APPLICABLE" and res["c1"].steps == []


def test_T5_resource_created_after_audit_is_out_of_scope(world):
    # same person, early request: a record that only appears months later must not leak back
    base(world)
    world.file("e1", "a.sql", "2026-03-10T00:00:00Z", sql_dump([account("account_8", "user_3", "bo@x.com")]))
    log = ('2026-09-10T10:00:00Z level=ERROR service=backup_service trace_id=t1 resource_id=backup_5 '
           'event=restore_candidate_evaluated eligible=true deletion_block=false correlation_key="user_1"\n')
    world.file("e2", "l.log", "2026-09-10T11:00:00Z", log)
    world.request("c1", "ana@x.com", requested="2026-03-01T00:00:00Z", reported="2026-03-02T00:00:00Z",
                  audit="2026-03-11T00:00:00Z")
    res, _ = world.run()
    assert all(s["resource_id"] != "backup_5" for s in res["c1"].steps)
