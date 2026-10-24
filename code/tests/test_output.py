"""Output-contract tests (edge-case matrix rows O*)."""
import csv

from ghost.audit import Plan, Step, finalize
from ghost.data import InputError, load_requests
from ghost.output import validate_output, write_output


def test_O1_roundtrip_with_quotes_commas_unicode(tmp_path):
    steps = [{"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_1",
              "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_1"}]
    p = tmp_path / "out.csv"
    write_output(p, [("case_10", "COMPLETE", steps), ("case_2", "NOT_APPLICABLE", [])])
    assert validate_output(p, ["case_2", "case_10"], {"evidence_1"}) == []
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0] == ["case_id", "deletion_status", "steps_json"]
    assert [r[0] for r in rows[1:]] == ["case_2", "case_10"]  # numeric case ordering


def test_O2_validator_catches_contract_violations(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text('case_id,deletion_status,steps_json\ncase_1,DONE,"[]"\ncase_1,COMPLETE,"{bad"\n', encoding="utf-8")
    errs = validate_output(p, ["case_1", "case_2"], set())
    joined = " ".join(errs)
    assert "duplicate" in joined and "bad status" in joined and "invalid JSON" in joined and "coverage" in joined


def test_O3_finalize_orders_phases_and_blanks_future_evidence():
    plan = Plan()
    plan.findings += [Step("REPORT_EXPOSED_PII", "support_platform", "support_1", "e1"),
                      Step("REPORT_REMAINING_RECORD", "search_service", "search_1", "e1"),
                      Step("REPORT_ACTIVE_DATA_FLOW", "integration_service", "data_flow_1", "e1")]
    plan.remediation += [Step("REDACT_RECORD", "support_platform", "support_1"),
                         Step("DELETE_RECORD", "search_service", "search_1"),
                         Step("DISABLE_DATA_FLOW", "integration_service", "data_flow_1")]
    plan.verification += [Step("VERIFY_DATA_FLOW_DISABLED", "integration_service", "data_flow_1", "e1"),
                          Step("VERIFY_RECORD_ABSENT", "search_service", "search_1"),
                          Step("VERIFY_RECORD_ABSENT", "account_service", "account_1", "e1")]
    status, steps = finalize(plan)
    assert status == "INCOMPLETE"
    assert [s["operation"] for s in steps] == [
        "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW",
        "DISABLE_DATA_FLOW", "DELETE_RECORD", "REDACT_RECORD",
        "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT", "VERIFY_DATA_FLOW_DISABLED"]
    assert [s["resource_id"] for s in steps[6:8]] == ["account_1", "search_1"]
    assert [s["step_no"] for s in steps] == list(range(1, 10))
    assert all(s["evidence_id"] == "" for s in steps[3:])


def test_O4_duplicate_requests_and_missing_columns(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
                 'case_1,a@b.com,2026-01-01T00:00:00Z,COMPLETE,2026-01-02T00:00:00Z,2026-01-03T00:00:00Z,"x, ""y"""\n'
                 "case_1,a@b.com,2026-01-01T00:00:00Z,COMPLETE,2026-01-02T00:00:00Z,2026-01-03T00:00:00Z,dup\n",
                 encoding="utf-8")
    reqs, warnings = load_requests(p)
    assert len(reqs) == 1 and reqs[0].request_text == 'x, "y"' and any("duplicate" in w for w in warnings)
    bad = tmp_path / "bad.csv"
    bad.write_text("case_id,email\ncase_1,a@b.com\n", encoding="utf-8")
    try:
        load_requests(bad)
        raise AssertionError("expected InputError")
    except InputError:
        pass
