"""Identity resolution and the resource-ownership graph.

Ownership of a resource is decided per subject from explicit evidence only:

* CONFIRMED  - a record key equals one of the subject's strong identifiers
               (current/historical email, email hash, user_id) or a catalog link.
* SUPPORTED  - phone-number match, or a deletion-workflow target of this request
               that also appears in an evidence file anchored to the subject.
* CANDIDATE  - deletion-workflow target of this request with no anchoring file
               and no competing owner.
Names are never sufficient on their own (similar-name decoys are common).
A resource whose keys match a different person is never assigned by timing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from .model import Observation, norm_email, norm_name, norm_phone, parse_time

CONFIRMED, SUPPORTED, CANDIDATE = "confirmed", "supported", "candidate"
TIER_RANK = {CONFIRMED: 3, SUPPORTED: 2, CANDIDATE: 1}

WORKFLOW_EVENTS = {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                   "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS", "RETAIN_RECORD"}
WINDOW_BEFORE = timedelta(hours=3)
WINDOW_AFTER = timedelta(hours=3)


def email_hash(email: str) -> str:
    return hashlib.sha256(norm_email(email).encode("utf-8")).hexdigest()


@dataclass
class Subject:
    user_id: str
    emails: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    names: set = field(default_factory=set)

    def strong_keys(self) -> set:
        return ({("email", e) for e in self.emails} | {("hash", h) for h in self.hashes}
                | {("user", self.user_id)})

    def phone_keys(self) -> set:
        return {("phone", p) for p in self.phones}

    def all_keys(self) -> set:
        return self.strong_keys() | self.phone_keys()


@dataclass
class Link:
    resource_id: str
    tier: str
    reasons: list = field(default_factory=list)
    evidence: set = field(default_factory=set)
    conflicts: list = field(default_factory=list)


class IdentityGraph:
    def __init__(self, identities: list[dict], audit_log: list[dict]):
        self.subjects: dict[str, Subject] = {}
        for row in identities:
            uid = row["user_id"].strip()
            s = self.subjects.setdefault(uid, Subject(uid))
            if row.get("email"):
                s.emails.add(norm_email(row["email"]))
            if row.get("email_hash"):
                s.hashes.add(row["email_hash"].strip().lower())
            if row.get("phone"):
                s.phones.add(norm_phone(row["phone"]))
            if row.get("name"):
                s.names.add(norm_name(row["name"]))
        for row in audit_log:
            s = self.subjects.setdefault(row["user_id"].strip(), Subject(row["user_id"].strip()))
            fname = row["field_name"].strip()
            for value in (row["old_value"], row["new_value"]):
                if not value:
                    continue
                if fname == "email":
                    s.emails.add(norm_email(value))
                elif fname == "email_hash":
                    s.hashes.add(value.strip().lower())
                elif fname == "phone":
                    s.phones.add(norm_phone(value))
                elif fname in ("name", "full_name"):
                    s.names.add(norm_name(value))
        for s in self.subjects.values():
            s.hashes |= {email_hash(e) for e in s.emails}
        # reverse index: identifier -> owners
        self.owner_index: dict[tuple, set] = {}
        self.phone_keys_all: set = {k for s in self.subjects.values() for k in s.phone_keys()}
        for s in self.subjects.values():
            for key in s.all_keys():
                self.owner_index.setdefault(key, set()).add(s.user_id)

    def resolve(self, email: str) -> Optional[Subject]:
        key = ("email", norm_email(email))
        owners = self.owner_index.get(key, set())
        if len(owners) == 1:
            return self.subjects[next(iter(owners))]
        return None

    def owners_of(self, obs: Observation) -> set:
        """Subjects a record points to via strong identifiers (phones alone are not ownership)."""
        owners: set = set()
        for key in (obs.keys | obs.exposed) - self.phone_keys_all:
            owners |= self.owner_index.get(key, set())
        if obs.stable_ref and obs.stable_ref in self.subjects:
            owners.add(obs.stable_ref)
        return owners


def workflow_targets(activity: list[dict], reported_at, audit_at) -> dict[str, list[dict]]:
    """Deletion-workflow activity around the time the workflow reported its status."""
    if reported_at is None:
        return {}
    lo, hi = reported_at - WINDOW_BEFORE, min(reported_at + WINDOW_AFTER, audit_at)
    out: dict[str, list[dict]] = {}
    for a in activity:
        if a["event_type"] not in WORKFLOW_EVENTS or a["source_system"].strip() != "deletion_worker":
            continue
        t = parse_time(a["event_time"])
        if t and lo <= t <= hi:
            out.setdefault(a["resource_id"].strip(), []).append(a)
    return out


def link_resources(graph: IdentityGraph, subject: Subject, observations: list[Observation],
                   workflow: dict[str, list[dict]], competing_workflows: dict[str, set]) -> dict[str, Link]:
    """Assign resources to the subject using only evidence available at audit time."""
    links: dict[str, Link] = {}
    strong, phones = subject.strong_keys(), subject.phone_keys()
    foreign_owned: dict[str, set] = {}

    def add(rid: str, tier: str, reason: str, evidence_id: str):
        link = links.setdefault(rid, Link(rid, tier))
        if TIER_RANK[tier] > TIER_RANK[link.tier]:
            link.tier = tier
        if reason not in link.reasons:
            link.reasons.append(reason)
        link.evidence.add(evidence_id)

    for obs in observations:
        keys = obs.keys | obs.exposed
        owners = graph.owners_of(obs)
        others = owners - {subject.user_id}
        if others:
            foreign_owned.setdefault(obs.resource_id, set()).update(others)
        if keys & strong or obs.stable_ref == subject.user_id:
            add(obs.resource_id, CONFIRMED, f"identifier match in {obs.evidence_id}", obs.evidence_id)
        elif keys & phones:
            # Phones are shared and recycled more often than emails: link only when the number
            # belongs to this subject alone and no stronger identifier names someone else.
            strong_owners = set()
            for key in keys - graph.phone_keys_all:
                strong_owners |= graph.owner_index.get(key, set())
            phone_owners = set()
            for key in keys & phones:
                phone_owners |= graph.owner_index.get(key, set())
            if phone_owners == {subject.user_id} and not (strong_owners - {subject.user_id}):
                add(obs.resource_id, SUPPORTED, f"unique phone match in {obs.evidence_id}", obs.evidence_id)

    # propagate through explicit resource links (search document -> source account, etc.)
    for obs in observations:
        for target in obs.linked_resources:
            if target in links and obs.resource_id not in links and not foreign_owned.get(obs.resource_id):
                add(obs.resource_id, SUPPORTED, f"linked to {target} in {obs.evidence_id}", obs.evidence_id)

    anchor_files = {e for l in links.values() if l.tier == CONFIRMED for e in l.evidence}
    files_by_resource: dict[str, set] = {}
    for obs in observations:
        files_by_resource.setdefault(obs.resource_id, set()).add(obs.evidence_id)

    for rid, events in workflow.items():
        if rid in links:
            links[rid].reasons.append("deletion-workflow target of this request")
            continue
        if foreign_owned.get(rid):
            continue  # belongs to someone else: never assign by timing
        rivals = competing_workflows.get(rid, set())
        shared = files_by_resource.get(rid, set()) & anchor_files
        if shared:
            add(rid, SUPPORTED, "workflow target appearing in subject-anchored evidence", sorted(shared)[0])
        elif not rivals:
            link = links.setdefault(rid, Link(rid, CANDIDATE))
            link.reasons.append("workflow target with no competing request")
            link.evidence.add(events[0]["activity_id"])
    for rid, owners in foreign_owned.items():
        if rid in links:
            links[rid].conflicts.append(f"also linked to {sorted(owners)}")
    return links
