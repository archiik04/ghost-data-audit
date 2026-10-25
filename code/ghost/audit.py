"""State reconstruction at audit_at and step planning."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .model import Coverage, Observation
from .policy import ANONYMIZE, BLOCK, DELETE, DISABLE, REDACT, RETAIN, Decision

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
PHASE = {**{o: "FINDING" for o in FINDING_ORDER}, **{o: "REMEDIATION" for o in REMEDIATION_ORDER},
         **{o: "VERIFICATION" for o in VERIFICATION_ORDER}}


@dataclass
class ResourceState:
    resource_id: str
    system: str
    present: Optional[bool] = None
    present_evidence: Optional[str] = None
    exposed: Optional[bool] = None
    exposure_evidence: Optional[str] = None
    enabled: Optional[bool] = None
    enabled_evidence: Optional[str] = None
    restore_allowed: Optional[bool] = None
    restore_evidence: Optional[str] = None
    retain_until: Optional[datetime] = None
    record_type: Optional[str] = None
    shared_context: Optional[bool] = None
    history: list = field(default_factory=list)


RETAINED_SYSTEMS = {"security_service", "legal_service"}


def reconstruct(resource_id: str, system: str, observations: list[Observation], coverage: list[Coverage],
                avail: dict, audit_at: datetime) -> ResourceState:
    """Latest evidence-backed state per aspect, using only evidence available at audit_at.

    Ordering is by when the evidence was captured (file capture time, or event time
    for activity rows), then by the in-file timestamp. Later captures win.
    """
    st = ResourceState(resource_id, system)
    facts = []  # (order_key, kind, value, evidence_id)
    for o in observations:
        when = avail.get(o.evidence_id, o.time)
        if when > audit_at or o.time > audit_at:
            # not available at audit time, or a row dated after audit_at (even inside an
            # earlier capture): neither may shape the audit-time state
            continue
        key = (when, o.time, o.evidence_id)
        if o.present is not None:
            facts.append((key, "present", o.present, o.evidence_id))
        if o.present is not False and (o.exposed or _pii_checked(o)):
            facts.append((key, "exposed", bool({e for e in o.exposed if e[0] != "user"}), o.evidence_id))
        if o.enabled is not None:
            facts.append((key, "enabled", o.enabled, o.evidence_id))
        if o.restore_allowed is not None:
            facts.append((key, "restore", o.restore_allowed, o.evidence_id))
        if o.retain_until:
            st.retain_until = o.retain_until
        if o.record_type and not st.record_type:
            st.record_type = o.record_type
        st.history.append(o)
    explicit_presence = any(f[1] == "present" for f in facts)
    for c in coverage:
        if c.system != system or resource_id in c.resources:
            continue
        # Full table dumps order normally against other evidence; partial snapshot bundles only
        # fill the gap when nothing explicit is known about the record.
        if c.label != "sql_snapshot" and explicit_presence:
            continue
        # Append-only retained records (security/legal) may simply post-date an older dump;
        # only an explicit observation can show that one disappeared.
        if system in RETAINED_SYSTEMS:
            continue
        when = avail.get(c.evidence_id, c.time)
        if when <= audit_at:
            facts.append(((when, c.time, c.evidence_id), "present", False, c.evidence_id))
    facts.sort(key=lambda f: f[0])
    for key, kind, value, eid in facts:
        if kind == "present":
            # several replicas captured together: presence in any copy wins
            if st.present is not None and st.present_evidence == eid and st.present and not value:
                continue
            st.present, st.present_evidence = value, eid
            if value is False:
                st.exposed = None
        elif kind == "exposed":
            if st.exposure_evidence == eid and st.exposed and not value:
                continue
            st.exposed, st.exposure_evidence = value, eid
        elif kind == "enabled":
            st.enabled, st.enabled_evidence = value, eid
        elif kind == "restore":
            st.restore_allowed, st.restore_evidence = value, eid
    return st


def _pii_checked(o: Observation) -> bool:
    """True when the observation states whether direct identifiers are present."""
    return o.pii_checked


@dataclass
class Step:
    op: str
    system_id: str
    resource_id: str
    evidence_id: str = ""

    def to_json(self, n: int) -> dict:
        return {"step_no": n, "phase": PHASE[self.op], "system_id": self.system_id,
                "resource_id": self.resource_id, "operation": self.op, "state": STATE[self.op],
                "evidence_id": self.evidence_id}


@dataclass
class Plan:
    findings: list = field(default_factory=list)
    remediation: list = field(default_factory=list)
    verification: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def plan_resource(st: ResourceState, decision: Decision, audit_at: datetime, plan: Plan) -> None:
    rid, sysid = st.resource_id, st.system
    action = decision.action
    if action == RETAIN and st.retain_until and st.retain_until < audit_at:
        action = DELETE
        plan.notes.append(f"{rid}: retention expired {st.retain_until.isoformat()} -> delete")

    if action == DISABLE:
        if st.enabled:
            plan.findings.append(Step("REPORT_ACTIVE_DATA_FLOW", sysid, rid, st.enabled_evidence or ""))
            plan.remediation.append(Step("DISABLE_DATA_FLOW", sysid, rid))
        plan.verification.append(Step("VERIFY_DATA_FLOW_DISABLED", sysid, rid, _ok(st.enabled is False, st.enabled_evidence)))
        return
    if action == BLOCK:
        if st.restore_allowed:
            plan.findings.append(Step("REPORT_RESTORE_RISK", sysid, rid, st.restore_evidence or ""))
            plan.remediation.append(Step("BLOCK_RESTORE", sysid, rid))
        plan.verification.append(Step("VERIFY_RESTORE_BLOCKED", sysid, rid, _ok(st.restore_allowed is False, st.restore_evidence)))
        return
    if action == RETAIN:
        plan.verification.append(Step("VERIFY_RECORD_PRESENT", sysid, rid, _ok(st.present is True, st.present_evidence)))
        if st.present is False:
            plan.notes.append(f"{rid}: policy requires retention but record is absent")
        return
    if action == DELETE:
        if st.present:
            plan.findings.append(Step("REPORT_REMAINING_RECORD", sysid, rid, st.present_evidence or ""))
            plan.remediation.append(Step("DELETE_RECORD", sysid, rid))
        plan.verification.append(Step("VERIFY_RECORD_ABSENT", sysid, rid, _ok(st.present is False, st.present_evidence)))
        return
    op_fix, op_verify = ("REDACT_RECORD", "VERIFY_RECORD_REDACTED") if action == REDACT else \
        ("ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED")
    if st.present is False:
        plan.verification.append(Step("VERIFY_RECORD_ABSENT", sysid, rid, st.present_evidence or ""))
        return
    if st.exposed or (st.present and st.exposed is None):
        plan.findings.append(Step("REPORT_EXPOSED_PII", sysid, rid, st.exposure_evidence or st.present_evidence or ""))
        plan.remediation.append(Step(op_fix, sysid, rid))
        plan.verification.append(Step(op_verify, sysid, rid))
    else:
        plan.verification.append(Step(op_verify, sysid, rid, st.exposure_evidence or st.present_evidence or ""))


def _ok(condition: bool, evidence: Optional[str]) -> str:
    return evidence or "" if condition else ""


def finalize(plan: Plan) -> tuple[str, list[dict]]:
    """Order steps per the contract and serialize. Status follows from the findings."""
    def ordered(steps, order):
        uniq = {(s.op, s.system_id, s.resource_id): s for s in steps}
        return sorted(uniq.values(), key=lambda s: (order.index(s.op), s.system_id, s.resource_id))

    findings = ordered(plan.findings, FINDING_ORDER)
    if findings:
        status = "INCOMPLETE"
        steps = findings + ordered(plan.remediation, REMEDIATION_ORDER) + ordered(plan.verification, VERIFICATION_ORDER)
        steps = [s if PHASE[s.op] == "FINDING" else Step(s.op, s.system_id, s.resource_id, "") for s in steps]
    else:
        status = "COMPLETE"
        steps = ordered(plan.verification, VERIFICATION_ORDER)
    return status, [s.to_json(i + 1) for i, s in enumerate(steps)]
