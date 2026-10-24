"""End-to-end orchestration: requests -> audited rows."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .audit import Plan, Step, finalize, plan_resource, reconstruct
from .data import Dataset, Request
from .evidence import build_index
from .identity import CANDIDATE, CONFIRMED, SUPPORTED, IdentityGraph, Link, link_resources, workflow_targets
from .model import EvidenceIndex, parse_time, system_for
from .policy import WORKFLOW_ACTION, Policy

IntentFn = Callable[[Request], "IntentResult"]

STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


@dataclass
class IntentResult:
    is_deletion: bool
    label: str
    source: str  # "model", "rules", "cache"
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    subject: Optional[str] = None
    intent: Optional[IntentResult] = None
    links: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


class Auditor:
    def __init__(self, dataset: Dataset, intent: IntentFn, index: Optional[EvidenceIndex] = None, vision=None):
        self.ds = dataset
        self.intent = intent
        self.index = index or build_index(dataset, vision=vision)
        self.graph = IdentityGraph(dataset.identities, dataset.audit_log)
        self.policy = Policy(dataset.rules)
        self.by_resource = self.index.by_resource()
        self.avail = dict(self.index.file_times)
        for a in dataset.activity:
            t = parse_time(a["event_time"])
            if t:
                self.avail[a["activity_id"]] = t
        self.activity_by_resource: dict[str, list] = {}
        for a in dataset.activity:
            self.activity_by_resource.setdefault(a["resource_id"].strip(), []).append(a)

    # ------------------------------------------------------------------ linking

    def _available(self, audit_at):
        return [o for o in self.index.observations if self.avail.get(o.evidence_id, o.time) <= audit_at]

    def link_all(self, requests: list[Request]) -> dict[str, tuple]:
        """Direct links per request, then a global one-to-one assignment of workflow targets."""
        prepared = {}
        windows = {}
        for r in requests:
            subject = self.graph.resolve(r.email)
            if subject is None or r.audit_at is None:
                prepared[r.case_id] = (r, subject, {})
                continue
            windows[r.case_id] = workflow_targets(self.ds.activity, r.reported_at, r.audit_at)
        rivals: dict[str, set] = {}
        for cid, w in windows.items():
            for rid in w:
                rivals.setdefault(rid, set()).add(cid)
        # Ownership is a timeless fact (who a record belongs to), so every evidence item may
        # establish it. Resource *state* is reconstructed strictly from evidence at audit_at.
        all_obs = self.index.observations
        for r in requests:
            if r.case_id in prepared:
                continue
            subject = self.graph.resolve(r.email)
            # direct evidence only; workflow targets are assigned globally below
            links = link_resources(self.graph, subject, all_obs, {}, {})
            prepared[r.case_id] = (r, subject, links)
        self._assign_workflow(prepared, windows, rivals)
        return prepared

    def _assign_workflow(self, prepared, windows, rivals):
        owned_elsewhere: dict[str, set] = {}
        for cid, (r, subject, links) in prepared.items():
            for rid, link in links.items():
                if link.tier == CONFIRMED:
                    owned_elsewhere.setdefault(rid, set()).add(cid)
        offsets = self._calibrate_offsets(prepared, windows)
        candidates = []  # (score, cid, rid)
        for cid, w in windows.items():
            r, subject, links = prepared[cid]
            anchor = {e for l in links.values() if l.tier == CONFIRMED for e in l.evidence}
            have_systems = {system_for(rid) for rid, l in links.items() if l.tier == CONFIRMED}
            for rid, events in w.items():
                if rid in links:
                    links[rid].reasons.append("deletion-workflow target of this request")
                    continue
                if owned_elsewhere.get(rid):
                    continue
                foreign = set()
                files = set()
                for o in self.by_resource.get(rid, []):
                    files.add(o.evidence_id)
                    foreign |= self.graph.owners_of(o)
                if foreign - {subject.user_id}:
                    continue
                system = system_for(rid)
                if system in have_systems:
                    continue  # the subject already has an evidenced record in this system
                score = 0.0
                ev = events[0]
                if files & anchor:
                    score += 4
                # Workflow-family consistency: a record the workflow fully remediated (and so
                # stripped of identifiers) belongs with requests whose evidenced records are
                # also compliant, not with requests whose own records visibly failed.
                if ev["reported_result"] == "SUCCESS" and system != "account_service":
                    failing = self._visible_failures(links, r.audit_at)
                    score += -2 if failing else 1
                if system == "account_service" and ev["event_type"] == "DELETE_RECORD":
                    if STATUS_RESULT.get(r.reported_status) == ev["reported_result"]:
                        score += 2
                t = parse_time(ev["event_time"])
                if t and r.reported_at:
                    expected = 0.0
                    for key in self._offset_keys(system, ev):
                        if key in offsets:
                            expected = offsets[key]
                            break
                    score -= abs((t - r.reported_at).total_seconds() - expected) / 3600.0
                candidates.append((score, cid, rid, sorted(files & anchor)))
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        taken_resource: set = set()
        taken_slot: set = set()
        for score, cid, rid, shared in candidates:
            system = system_for(rid)
            slot = (cid, system)
            if rid in taken_resource or slot in taken_slot:
                continue
            contested = len(rivals.get(rid, set())) > 1
            tier = SUPPORTED if shared else CANDIDATE
            r, subject, links = prepared[cid]
            reason = "workflow target in subject-anchored evidence" if shared else "workflow target by timing"
            if contested:
                reason += f" (contested by {len(rivals[rid]) - 1} other request(s); resolved by one-to-one assignment)"
            links[rid] = Link(rid, tier, [reason], set(shared) or {self.activity_by_resource[rid][0]["activity_id"]})
            taken_resource.add(rid)
            taken_slot.add(slot)

    # ------------------------------------------------------------------ auditing

    def audit(self, requests: list[Request], targets: Optional[set] = None) -> list[CaseResult]:
        """Audit ``targets`` (default: all). Every request still competes in resource linking."""
        prepared = self.link_all(requests)
        results = []
        for r in requests:
            if targets is not None and r.case_id not in targets:
                continue
            req, subject, links = prepared[r.case_id]
            intent = self.intent(r)
            if not intent.is_deletion:
                results.append(CaseResult(r.case_id, "NOT_APPLICABLE", [], subject.user_id if subject else None, intent))
                continue
            plan = Plan()
            if subject is None:
                plan.notes.append("request email does not resolve to a known subject")
            for rid, link in sorted(links.items()):
                system = system_for(rid)
                if system is None:
                    continue
                st = reconstruct(rid, system, self.by_resource.get(rid, []), self.index.coverage, self.avail,
                                 r.audit_at)
                actions = {WORKFLOW_ACTION[a["event_type"]] for a in self.activity_by_resource.get(rid, [])
                           if a["event_type"] in WORKFLOW_ACTION and parse_time(a["event_time"]) <= r.audit_at}
                decision = self.policy.decide(system, st.record_type, actions, st.shared_context)
                first = self._first_seen(rid)
                if first is None or first > r.audit_at:
                    plan.notes.append(f"{rid}: first recorded after audit_at; did not exist at audit time")
                    continue
                if st.present is None and st.enabled is None and st.restore_allowed is None:
                    if system in ("security_service", "legal_service"):
                        # retained record known only from later captures: its availability is still
                        # a required end state, verified after remediation
                        st.present = True
                    elif system == "account_service":
                        # the subject's profile must end up absent whatever we could observe; with no
                        # state evidence this is a check to perform, never a citation or a finding
                        plan.verification.append(Step("VERIFY_RECORD_ABSENT", system, rid, ""))
                        plan.notes.append(f"{rid}: no state evidence; verification required")
                        continue
                    else:
                        plan.notes.append(f"{rid}: no state evidence at audit time; not planned (unresolved)")
                        continue
                if self._historical(rid, link, r):
                    plan.notes.append(f"{rid}: only evidenced before the request and not touched by its "
                                      "workflow; treated as history outside the request scope")
                    continue
                plan_resource(st, decision, r.audit_at, plan)
            status, steps = finalize(plan)
            results.append(CaseResult(r.case_id, status, steps, subject.user_id if subject else None, intent,
                                      links, plan.notes))
        return results


    def _first_seen(self, rid):
        """Earliest in-record timestamp of any observation or workflow event for the resource."""
        times = [o.time for o in self.by_resource.get(rid, [])]
        times += [parse_time(a["event_time"]) for a in self.activity_by_resource.get(rid, [])]
        times = [t for t in times if t is not None]
        return min(times) if times else None

    def _visible_failures(self, links, audit_at) -> bool:
        """Does any identifier-confirmed record of this subject still violate policy at audit_at?"""
        for rid, link in links.items():
            if link.tier != CONFIRMED:
                continue
            system = system_for(rid)
            if system in ("security_service", "legal_service", None):
                continue
            st = reconstruct(rid, system, self.by_resource.get(rid, []), self.index.coverage, self.avail, audit_at)
            if st.enabled or st.restore_allowed or (st.present and system in (
                    "account_service", "search_service", "export_service", "marketing_platform")) or st.exposed:
                return True
        return False

    def _calibrate_offsets(self, prepared, windows) -> dict:
        """Typical delay between a workflow step and the workflow's status report.

        Learned at runtime from workflow targets whose ownership is already confirmed by
        identifiers (no labels involved), per (system, event type[, result]).
        """
        samples: dict[tuple, list] = {}
        for cid, w in windows.items():
            r, subject, links = prepared[cid]
            for rid, events in w.items():
                link = links.get(rid)
                if not link or link.tier != CONFIRMED or not r.reported_at:
                    continue
                ev = events[0]
                t = parse_time(ev["event_time"])
                if t is None:
                    continue
                delta = (t - r.reported_at).total_seconds()
                for key in self._offset_keys(system_for(rid), ev):
                    samples.setdefault(key, []).append(delta)
        return {k: sorted(v)[len(v) // 2] for k, v in samples.items() if len(v) >= 2}

    @staticmethod
    def _offset_keys(system, ev) -> list:
        """Most to least specific grouping; the detail template separates workflow variants."""
        template = re.sub(r"[a-z_]+_\d+", "<id>", ev["details"])
        et, res = ev["event_type"], ev["reported_result"]
        return [(system, et, res, template), (et, res, template), (system, et, res), (system, et)]

    def _historical(self, rid, link, r) -> bool:
        """Evidence that predates the request, for a record its workflow never touched, is history."""
        if r.requested_at is None or any("workflow" in reason for reason in link.reasons):
            return False
        # identity-catalog links count as sightings; other requests' workflow events do not
        seen = [self.avail.get(o.evidence_id, o.time) for o in self.by_resource.get(rid, [])
                if o.source != "activity" or o.keys]
        seen = [t for t in seen if t is not None and t <= r.audit_at]
        return bool(seen) and max(seen) < r.requested_at


def steps_json(steps: list) -> str:
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
