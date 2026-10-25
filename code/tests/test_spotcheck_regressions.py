"""Regressions for defects found by the evaluation-set spot-check (edge-case rows S16, I15, I16, S17, T11, P10)."""
import json

from conftest import ops
from test_pipeline import account, base, sql_dump

from ghost.evidence import parse_log_line


def test_S16_rfc5424_syslog_lines_keep_trace_and_service():
    ts, d = parse_log_line('2026-03-25T08:52:11Z <14>1 node-9 account_service tr-58 - - level=INFO '
                           'resource_id="account_3" event="profile_lookup_completed" rows="0" http_status="404"')
    assert d["trace_id"] == "tr-58" and d["service"] == "account_service" and d["resource_id"] == "account_3"


def test_S16b_syslog_trace_join_links_lookup_to_resource(world):
    base(world)
    log = ('2026-09-10T10:00:00Z <15>1 node-9 search_service tr-1 - - level=DEBUG event="index_query_started" '
           'match_key="ANA@X.COM"\n'
           '2026-09-10T10:00:02Z <14>1 node-9 search_service tr-1 - - level=INFO resource_id="search_4" '
           'event="index_query_completed" result="hit"\n')
    world.file("e1", "sys.log", "2026-09-10T11:00:00Z", log).request("c1", "ana@x.com")
    res, _ = world.run()
    assert ("REPORT_REMAINING_RECORD", "search_4", "e1") in ops(res["c1"])


def test_I15_ledger_lookup_value_establishes_owner(world):
    # the owner of a ledger row may be given as lookup_value instead of subject_reference
    base(world)
    bundle = {"exported_at": "2026-09-10T00:00:00Z", "snapshots": [{"records": [
        {"recorded_at": "2026-09-09T00:00:00Z", "system": "account_service", "resource_id": "account_9",
         "record_type": "USER_PROFILE", "result": "FOUND", "lookup_value": "bo@x.com"}]}]}
    world.file("e1", "b.json", "2026-09-10T00:00:00Z", json.dumps(bundle))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_9")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert "account_9" not in res["c1"].links  # it is Bo's account, never Ana's by timing


def test_I16_non_account_record_needs_anchor_for_timing_link(world):
    base(world).act("w1", "2026-09-02T11:30:00Z", "ANONYMIZE_RECORD", "analytics_platform", "analytics_5")
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert "analytics_5" not in res["c1"].links


def test_S17_inventory_scope_proves_absence_only_when_nothing_explicit(world):
    base(world)
    inv = ("CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT);\n"
           "INSERT INTO snapshot_scope VALUES ('account_service','2026-09-10T00:00:00Z');\n"
           "CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, email TEXT);\n")
    world.file("e1", "inv.sql", "2026-09-10T00:00:00Z", inv)
    world.file("e0", "s.json", "2026-09-09T00:00:00Z", json.dumps({"service_exports": {"security_service": {
        "audit_records": [{"resource_id": "audit_record_1", "subject_reference": "user_1",
                           "retention_until": "2030-01-01T00:00:00Z"}]}}}))
    world.act("w1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_1", "SUCCESS")
    world.request("c1", "ana@x.com", status="COMPLETE")
    res, _ = world.run()
    assert ("VERIFY_RECORD_ABSENT", "account_1", "e1") in ops(res["c1"])


def test_T11_retained_record_seen_before_request_is_still_verified(world):
    base(world).file("e0", "s.json", "2026-08-01T00:00:00Z", json.dumps({"service_exports": {"security_service": {
        "audit_records": [{"resource_id": "audit_record_1", "subject_reference": "user_1",
                           "retention_until": "2030-01-01T00:00:00Z"}]}}}))
    world.file("e1", "a.sql", "2026-09-10T00:00:00Z", sql_dump([account("account_1", "user_1", "ana@x.com")]))
    world.request("c1", "ana@x.com")
    res, _ = world.run()
    assert ("VERIFY_RECORD_PRESENT", "audit_record_1", "") in ops(res["c1"])
