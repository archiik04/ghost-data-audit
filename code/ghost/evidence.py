"""Evidence extraction: every supported format -> Observation / Coverage rows.

Parsers are deliberately generic (field-name driven) so that unseen files in
the same families are handled without file-specific code. A file that cannot be
read is recorded as a ParseIssue, never silently dropped.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from .model import (
    Coverage, EvidenceIndex, Observation, ParseIssue, classify_identifier, identifiers_in_text,
    is_nullish, is_resource_id, norm_name, parse_time, system_for,
)

# Field vocabularies ---------------------------------------------------------

NAME_FIELDS = {"full_name", "display_name", "name", "requester_name", "customer_name", "owner_name"}
EMAIL_FIELDS = {"email", "owner_email", "customer_email", "requester_email", "contact_email", "uploaded_by"}
PHONE_FIELDS = {"phone", "owner_phone", "requester_phone", "customer_phone"}
HASH_FIELDS = {"email_hash"}
REF_FIELDS = {"customer_reference", "user_id", "subject_reference", "subject_ref"}
KEY_FIELDS = {"lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "correlation_key", "query",
              "matched_value", "subject_selector", "lookup_value"}
TEXT_FIELDS = {"message_text", "message_excerpt", "message", "body", "note_text"}
TIME_FIELDS = ("observed_at", "recorded_at", "updated_at", "indexed_at", "captured_at", "event_time")

TRUE = {"true", "1", "yes", "enabled", "active", "allow", "allowed"}
FALSE = {"false", "0", "no", "disabled", "stopped", "deny", "denied", "blocked"}


def to_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in TRUE:
            return True
        if low in FALSE:
            return False
    return None


def to_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# Generic record -> Observation ---------------------------------------------


def record_to_obs(rec: dict, evidence_id: str, default_time: datetime, source: str,
                  extra_keys: Iterable = (), replica: Optional[str] = None,
                  default_present: Optional[bool] = True) -> Optional[Observation]:
    """Convert one flat-ish record that carries a resource_id."""
    rid = rec.get("resource_id")
    if not isinstance(rid, str) or not is_resource_id(rid.strip()):
        return None
    rid = rid.strip()
    flat = dict(rec)
    for nested in ("profile", "document", "record", "customer"):
        if isinstance(rec.get(nested), dict):
            for k, v in rec[nested].items():
                flat.setdefault(k, v)
    time = default_time
    for f in TIME_FIELDS:
        t = parse_time(flat.get(f)) if isinstance(flat.get(f), str) else None
        if t:
            time = min(t, default_time) if default_time else t
            break
    obs = Observation(evidence_id=evidence_id, time=time, resource_id=rid,
                      system=system_for(rid) or str(flat.get("system") or flat.get("origin_system") or "unknown"),
                      source=source, present=default_present, replica=replica)
    for key in extra_keys:
        obs.keys.add(key)
    obs.pii_checked = any(f in flat for f in NAME_FIELDS | EMAIL_FIELDS | PHONE_FIELDS | HASH_FIELDS)

    for f, v in flat.items():
        if is_nullish(v) or isinstance(v, (dict, list)):
            continue
        if f in NAME_FIELDS and isinstance(v, str):
            n = norm_name(v)
            if n:
                obs.names.add(n)
                obs.exposed.add(("name", n))
        elif f in EMAIL_FIELDS | PHONE_FIELDS | HASH_FIELDS:
            ident = classify_identifier(str(v))
            if ident:
                obs.exposed.add(ident)
                obs.keys.add(ident)
        elif f in REF_FIELDS:
            ident = classify_identifier(str(v))
            if ident:
                obs.keys.add(ident)
                if ident[0] == "user":
                    obs.stable_ref = ident[1]
                elif f != "subject_reference":
                    obs.exposed.add(ident)
        elif f in KEY_FIELDS:
            ident = classify_identifier(str(v))
            if ident:
                obs.keys.add(ident)
            elif isinstance(v, str) and is_resource_id(v.strip()) and v.strip() != rid:
                obs.linked_resources.add(v.strip())
        elif f in TEXT_FIELDS and isinstance(v, str):
            for ident in identifiers_in_text(v):
                obs.exposed.add(ident)
                obs.keys.add(ident)
        elif f == "source_resource_id" and isinstance(v, str) and is_resource_id(v):
            obs.linked_resources.add(v)

    # state fields
    status = to_int(flat.get("status_code", flat.get("http_status")))
    if status is not None:
        obs.present = status < 400
    rows = to_int(flat.get("rows", flat.get("total_hits", flat.get("total"))))
    if rows is not None and status is None:
        obs.present = rows > 0
    if "profile" in rec and rec["profile"] is None and status is None:
        obs.present = False
    res = str(flat.get("result", "")).strip().lower()
    if res in ("hit", "found", "match"):
        obs.present = True
    elif res in ("miss", "no_hit", "not_found", "none"):
        obs.present = False
    for f in ("archive_present", "download_available", "available"):
        if f in flat and to_bool(flat[f]) is not None:
            obs.present = to_bool(flat[f])
    if "enabled" in flat and to_bool(flat["enabled"]) is not None:
        obs.enabled = to_bool(flat["enabled"])
    if "restore_allowed" in flat and to_bool(flat["restore_allowed"]) is not None:
        obs.restore_allowed = to_bool(flat["restore_allowed"])
    if "eligible" in flat or "deletion_block" in flat:
        eligible = to_bool(flat.get("eligible"))
        block = to_bool(flat.get("deletion_block"))
        if eligible is not None or block is not None:
            obs.restore_allowed = bool(eligible) and not bool(block)
    if flat.get("decision") and to_bool(flat["decision"]) is not None and obs.system == "backup_service":
        obs.restore_allowed = to_bool(flat["decision"])
    if "marker_found" in flat and to_bool(flat["marker_found"]) is False:
        obs.present = True
    di = to_int(flat.get("direct_identifiers"))
    if di == 0:
        obs.exposed = {e for e in obs.exposed if e[0] != "hash"} if obs.exposed else set()
    retain = flat.get("retention_until") or flat.get("retain_until") or flat.get("retained_until")
    if retain and not is_nullish(retain):
        obs.retain_until = parse_time(str(retain))
    if str(flat.get("storage_class", "")).lower() == "ticket_attachment":
        obs.record_type = "SUPPORT_ATTACHMENT"
    if isinstance(flat.get("record_type"), str):
        obs.record_type = flat["record_type"]
    if isinstance(flat.get("event_code") or flat.get("event_name"), str):
        obs.note = str(flat.get("event_code") or flat.get("event_name"))
    if isinstance(flat.get("file"), str) and obs.system == "support_platform":
        obs.record_type = obs.record_type or "SUPPORT_ATTACHMENT"
    if isinstance(flat.get("connector_name"), str):
        obs.note = flat["connector_name"]
    return obs


# Ledger-style rows: (system, resource, record_type, RESULT, subject) ------------

LEDGER_PRESENT = {"FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "BLOCKED", "DISABLED", "PRESENT",
                  "RETAINED"}
LEDGER_ABSENT = {"NOT_FOUND", "ABSENT", "DELETED"}


def ledger_to_obs(row: dict, evidence_id: str, default_time: datetime, source: str) -> Optional[Observation]:
    rid = (row.get("resource_id") or "").strip()
    if not is_resource_id(rid):
        return None
    result = (row.get("result") or "").strip().upper()
    time = parse_time(row.get("recorded_at") or row.get("time") or "") or default_time
    time = min(time, default_time)
    obs = Observation(evidence_id=evidence_id, time=time, resource_id=rid,
                      system=system_for(rid) or (row.get("system") or "unknown"), source=source,
                      record_type=(row.get("record_type") or "").strip().upper() or None, result=result)
    subject = (row.get("subject_reference") or "").strip()
    ident = classify_identifier(subject)
    if ident:
        obs.keys.add(ident)
        if ident[0] == "user":
            obs.stable_ref = ident[1]
    obs.pii_checked = result in ("IDENTIFIABLE", "ANONYMIZED", "REDACTED")
    if result in LEDGER_PRESENT:
        obs.present = True
    elif result in LEDGER_ABSENT:
        obs.present = False
    if result == "IDENTIFIABLE" and ident:
        obs.exposed.add(ident)
    if result == "FOUND" and ident and ident[0] != "user":
        obs.exposed.add(ident)
    if obs.system == "integration_service" and result in ("ACTIVE", "DISABLED"):
        obs.enabled = result == "ACTIVE"
    if obs.system == "backup_service" and result in ("ACTIVE", "BLOCKED"):
        obs.restore_allowed = result == "ACTIVE"
    return obs


# SQL ---------------------------------------------------------------------------

TABLE_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
    "search_documents": "search_service",
}


def table_system(table: str) -> Optional[str]:
    for base, system in TABLE_SYSTEM.items():
        if table == base or table.startswith(base + "_"):
            return system
    return None


def parse_sql(path: Path, evidence_id: str, file_time: datetime, index: EvidenceIndex) -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(path.read_text(encoding="utf-8"))
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        scope: dict[str, datetime] = {}
        if "snapshot_scope" in tables:
            for system, captured in con.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
                scope[system] = parse_time(captured) or file_time
        per_system: dict[str, set] = {}
        covered: set[str] = set(scope)
        for table in tables:
            if table == "snapshot_scope":
                continue
            cur = con.execute(f'SELECT * FROM "{table}"')
            cols = [d[0] for d in cur.description]
            tsys = table_system(table)
            if tsys and not scope:
                covered.add(tsys)
            for values in cur.fetchall():
                rec = dict(zip(cols, values))
                if isinstance(rec.get("record_json"), str):
                    try:
                        rec.update(json.loads(rec["record_json"]))
                    except json.JSONDecodeError:
                        index.issues.append(ParseIssue(evidence_id, str(path), "partial",
                                                       f"bad record_json in {table}"))
                obs = record_to_obs(rec, evidence_id, file_time, "sql", replica=table)
                if obs is None:
                    continue
                index.observations.append(obs)
                per_system.setdefault(obs.system, set()).add(obs.resource_id)
                if not scope:
                    covered.add(obs.system)
        # Inventory extracts (snapshot_scope) list selected rows only, so absence there proves
        # nothing. Replica-topology dumps hold whole tables: absence of a known resource is meaningful.
        for system in ([] if scope else covered):
            index.coverage.append(Coverage(evidence_id, scope.get(system, file_time), system,
                                           per_system.get(system, set()), label="sql_snapshot"))
    finally:
        con.close()


# JSON ----------------------------------------------------------------------------


def walk_json(node, evidence_id: str, time: datetime, index: EvidenceIndex, keys: tuple = ()) -> None:
    if isinstance(node, list):
        for item in node:
            walk_json(item, evidence_id, time, index, keys)
        return
    if not isinstance(node, dict):
        return
    for f in ("captured_at", "observed_at", "exported_at"):
        t = parse_time(node.get(f)) if isinstance(node.get(f), str) else None
        if t and "resource_id" not in node:
            time = min(t, time)
            break
    if "resource_id" in node and "result" in node and "record_type" in node:
        obs = ledger_to_obs(node, evidence_id, time, "ledger")
        if obs:
            index.observations.append(obs)
        return
    if "resource_id" in node:
        query_keys = keys
        ident = classify_identifier(str(node.get("query", "")))
        if ident:
            query_keys = keys + (ident,)
        hits = node.get("hits")
        if isinstance(hits, dict):
            docs = hits.get("documents") or []
            obs = record_to_obs({k: v for k, v in node.items() if k != "hits"}, evidence_id, time, "json",
                                extra_keys=query_keys)
            if obs:
                total = to_int(hits.get("total"))
                own = [d for d in docs if isinstance(d, dict) and d.get("resource_id") == obs.resource_id]
                obs.present = bool(own) if total is None else total > 0 and (bool(own) or not docs)
                for d in own:
                    sub = record_to_obs(d, evidence_id, obs.time, "json")
                    if sub:
                        obs.exposed |= sub.exposed
                        obs.keys |= sub.keys
                        obs.names |= sub.names
                        obs.stable_ref = obs.stable_ref or sub.stable_ref
                index.observations.append(obs)
            for d in docs:
                if isinstance(d, dict) and d.get("resource_id") != node.get("resource_id"):
                    sub = record_to_obs(d, evidence_id, time, "json", extra_keys=query_keys)
                    if sub:
                        index.observations.append(sub)
            return
        obs = record_to_obs(node, evidence_id, time, "json", extra_keys=query_keys)
        if obs:
            index.observations.append(obs)
        return
    # snapshot container: a full dump of one data store
    if isinstance(node.get("records"), list) and isinstance(node.get("data_store"), str):
        before = len(index.observations)
        walk_json(node["records"], evidence_id, time, index, keys)
        rids = {o.resource_id for o in index.observations[before:]}
        index.coverage.append(Coverage(evidence_id, time, node["data_store"], rids, label="json_snapshot"))
        return
    for value in node.values():
        if isinstance(value, (dict, list)):
            walk_json(value, evidence_id, time, index, keys)


# Logs ------------------------------------------------------------------------------

KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(.*)$")
BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[([\w\-]+)\]\s+\[([\w\-]+)\]\s+(\w+):\s*(.*)$")
PIPE_RE = re.compile(r"^\|?\s*(\w+)\s*\|\s*([\w\-]+)\s*\|\s*([\w\-]+)\s*\|\s*(\w+)\s*\|\s*(.*)$")


def parse_kv(text: str) -> dict:
    out = {}
    for k, v in KV_RE.findall(text):
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1].replace('\\"', '"')
        out[k] = v
    return out


def parse_log_line(line: str) -> Optional[tuple[datetime, dict]]:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = parse_time(m.group(1)), m.group(2).strip()
    if rest.startswith("{"):
        try:
            data = json.loads(rest)
        except json.JSONDecodeError:
            return None
        return ts, {k: (str(v) if not isinstance(v, (dict, list)) else v) for k, v in data.items()}
    b = BRACKET_RE.match(rest) or PIPE_RE.match(rest)
    if b:
        data = parse_kv(b.group(5))
        data.update({"level": b.group(1), "service": b.group(2), "trace_id": b.group(3), "event": b.group(4)})
        return ts, data
    data = parse_kv(rest)
    if "message" in data and "=" in data["message"]:
        inner = parse_kv(data.pop("message"))
        data.update(inner)
        if "requestId" in data:
            data.setdefault("trace_id", data["requestId"])
    return ts, data


def parse_log(path: Path, evidence_id: str, file_time: datetime, index: EvidenceIndex) -> None:
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_log_line(raw)
        if parsed:
            lines.append(parsed)
    trace_keys: dict[str, set] = {}
    for _, d in lines:
        tid = d.get("trace_id")
        if not tid:
            continue
        for f in KEY_FIELDS | REF_FIELDS | EMAIL_FIELDS:
            ident = classify_identifier(d.get(f, "")) if isinstance(d.get(f), str) else None
            if ident:
                trace_keys.setdefault(tid, set()).add(ident)
    for ts, d in lines:
        if "record_type" in d and "result" in d and "resource_id" in d:
            obs = ledger_to_obs(d, evidence_id, min(ts, file_time), "log")
        else:
            lookup = d.get("lookup_key", "")
            if not d.get("resource_id") and is_resource_id(lookup):
                d["resource_id"] = lookup
            obs = record_to_obs(d, evidence_id, min(ts, file_time), "log",
                                extra_keys=trace_keys.get(d.get("trace_id", ""), ()),
                                default_present=None)
            if obs is None:
                continue
            event = d.get("event", "")
            obs.event = event
            if event.endswith("_read_completed") and obs.present is None:
                obs.present = True
            if event.endswith("_pii_scan") or event == "analytics_identity_scan":
                obs.present = True
                obs.pii_checked = True
                if d.get("matched_value"):
                    ident = classify_identifier(d["matched_value"])
                    if ident:
                        obs.exposed.add(ident)
                if event == "attachment_pii_scan":
                    obs.record_type = "SUPPORT_ATTACHMENT"
            if event in ("analytics_profile_read", "message_read"):
                obs.pii_checked = True
            if event in ("analytics_profile_read", "message_read", "audit_record_read_completed",
                           "contact_query_completed", "contact_lookup_completed", "connector_heartbeat",
                           "connector_config_loaded", "restore_candidate_evaluated",
                           "restore_authorization_completed") and obs.present is None:
                obs.present = True
            if event == "contact_lookup_completed":
                ck = classify_identifier(d.get("correlation_key", ""))
                if ck and ck[0] in ("hash", "email"):
                    obs.exposed.add(ck)
                if to_bool(d.get("email_opt_in")) is not None:
                    obs.record_type = obs.record_type or "MARKETING_CONTACT"
            if obs.present is None and not (obs.exposed or obs.enabled is not None or obs.restore_allowed is not None):
                # mention without state (e.g. *_started lines): keep as a pure link
                pass
        if obs:
            index.observations.append(obs)


# Text / PDF ----------------------------------------------------------------------

HANDOVER_RE = re.compile(r"^\s*-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*)$")


def parse_text(path: Path, evidence_id: str, file_time: datetime, index: EvidenceIndex) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    matched = False
    for line in text.splitlines():
        m = HANDOVER_RE.match(line)
        if m:
            matched = True
            row = dict(zip(("recorded_at", "system", "resource_id", "record_type", "result", "subject_reference"),
                           (g.strip() for g in m.groups())))
            obs = ledger_to_obs(row, evidence_id, file_time, "ledger")
            if obs:
                index.observations.append(obs)
    if not matched:
        document_mentions(text, evidence_id, file_time, index, "text")


def document_mentions(text: str, evidence_id: str, time: datetime, index: EvidenceIndex, source: str,
                      aliases: Optional[dict] = None) -> None:
    """Free-form documents: each mentioned resource is present and tied to the identifiers in it."""
    from .model import RESOURCE_RE
    idents = identifiers_in_text(text)
    rids = set(RESOURCE_RE.findall(text))
    for alias, rid in (aliases or {}).items():
        if alias.startswith("__"):
            continue
        if alias and re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", text):
            rids.add(rid)
    users = {i[1] for i in idents if i[0] == "user"}
    emails = {i[1] for i in idents if i[0] == "email"}
    # A document naming several people cannot establish who owns a resource it mentions as a
    # whole; in that case only the identifiers in the resource's own table row/section count.
    single_subject = len(users) <= 1 and len(emails) <= 1
    if single_subject and len(users) == 1:
        # a deleted-account reference registry shows a tombstone under its stable user_id
        for alias, rid in (aliases or {}).get("__user_ref__", {}).items():
            if alias in users and re.search(r"\breference\b", text, re.I):
                rids.add(rid)
    segments = resource_segments(text, rids)
    for rid in sorted(rids):
        obs = Observation(evidence_id=evidence_id, time=time, resource_id=rid, system=system_for(rid) or "unknown",
                          source=source, present=True)
        segment = segments.get(rid, "")
        local = identifiers_in_text(segment) if segment else set()
        local_single = len({i[1] for i in local if i[0] == "user"}) <= 1 and             len({i[1] for i in local if i[0] == "email"}) <= 1
        scope = idents if single_subject else (local if local_single else set())
        local_users = {i[1] for i in scope if i[0] == "user"}
        obs.keys |= scope
        if len(local_users) == 1:
            obs.stable_ref = next(iter(local_users))
        if obs.system not in ("security_service", "legal_service"):
            # a tombstone may keep its user_id; any other identifier on it is exposure
            obs.exposed |= {i for i in scope if i[0] != "user"}
            obs.pii_checked = bool(single_subject or (segment and local_single))
        if obs.system == "support_platform" and source == "pdf":
            obs.record_type = "SUPPORT_ATTACHMENT"  # a PDF stored on a ticket is an attachment
        if source == "image":
            screen_state(obs, segment or text, text)
        index.observations.append(obs)


ACTIVE_WORDS = re.compile(r"\b(healthy|active|enabled|running|continuous|live|delivered|syncing)\b", re.I)
INACTIVE_WORDS = re.compile(r"\b(disabled|paused|stopped|inactive|suspended|deactivated)\b", re.I)
BLOCKED_WORDS = re.compile(r"restore (blocked|disabled|denied)|deletion[- ]block|not restorable|quarantined|"
                           r"restore access: (none|revoked)", re.I)


def resource_segments(text: str, rids: set) -> dict:
    """Text belonging to each resource: from each mention up to the next mention of another resource.

    Screens and tables list one record per row/panel, so identifiers that follow a resource id
    (before the next id) belong to that record.
    """
    if not rids:
        return {}
    pattern = re.compile("|".join(re.escape(r) for r in sorted(rids, key=len, reverse=True)))
    hits = list(pattern.finditer(text))
    out: dict[str, str] = {}
    for n, m in enumerate(hits):
        end = hits[n + 1].start() if n + 1 < len(hits) else min(len(text), m.end() + 600)
        out[m.group(0)] = out.get(m.group(0), "") + "\n" + text[m.end():end]
    return out


def screen_state(obs: Observation, segment: str, whole: str) -> None:
    """Process state shown on an application screen (connectors, backups)."""
    if obs.system == "integration_service":
        if INACTIVE_WORDS.search(segment):
            obs.enabled = False
        elif ACTIVE_WORDS.search(segment):
            obs.enabled = True
    elif obs.system == "backup_service":
        if BLOCKED_WORDS.search(segment):
            obs.restore_allowed = False
        elif re.search(r"\b(restore|recovery)\b", whole, re.I):
            obs.restore_allowed = True  # listed as a restorable archive in a recovery console


def pdf_text(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
        with fitz.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    except ImportError:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


# Activity stream -----------------------------------------------------------------

LINK_RE = re.compile(r"linked (\S+) \(([^)]+)\) to (\S+) and (user_\d+)|confirmed (\S+) \(([^)]+)\) belongs only to (\S+) and (user_\d+)")


def activity_observations(activity: list[dict], index: EvidenceIndex) -> dict:
    """Activity rows are claims; only identity links and explicit state checks become observations."""
    aliases: dict[str, str] = {}
    for a in activity:
        t = parse_time(a["event_time"])
        if t is None:
            continue
        rid, et, details = a["resource_id"].strip(), a["event_type"], a["details"]
        if et == "LINK_RESOURCE_IDENTITY":
            m = LINK_RE.search(details)
            if m:
                g = [x for x in m.groups() if x]
                target, alias, email, user = g[0], g[1], g[2], g[3]
                # aliases such as invoice numbers or file names; never identity-like values
                if alias != target and classify_identifier(alias) is None and not is_resource_id(alias):
                    aliases[alias] = target
                elif target.startswith("tombstone_") and re.fullmatch(r"user_\d+", alias):
                    aliases.setdefault("__user_ref__", {})[alias] = target
                obs = Observation(a["activity_id"], t, target, system_for(target) or a["target_system"], "activity")
                obs.keys |= {("email", email.lower()), ("user", user)}
                if "belongs only to" in details:
                    obs.record_type = "SUPPORT_ATTACHMENT"
                index.observations.append(obs)
        elif et == "CHECK_DATA_FLOW" and is_resource_id(rid):
            obs = Observation(a["activity_id"], t, rid, system_for(rid) or a["target_system"], "activity",
                              present=True)
            obs.enabled = "remains enabled" in details or "active" in details.lower()
            index.observations.append(obs)
        elif et == "CHECK_RESTORE_ACCESS" and is_resource_id(rid):
            obs = Observation(a["activity_id"], t, rid, system_for(rid) or a["target_system"], "activity",
                              present=True)
            obs.restore_allowed = "can still be restored" in details
            index.observations.append(obs)
    return aliases


# Driver ---------------------------------------------------------------------------

VisionFn = Callable[[Path, str], Optional[str]]


def build_index(dataset, vision: Optional[VisionFn] = None) -> EvidenceIndex:
    index = EvidenceIndex()
    aliases = activity_observations(dataset.activity, index)
    for row in dataset.evidence_files:
        eid = row["file_id"].strip()
        path = dataset.root / row["file_path"].strip()
        ftime = parse_time(row["captured_at"])
        if ftime is None:
            index.issues.append(ParseIssue(eid, str(path), "unreadable", "manifest captured_at unparseable"))
            continue
        index.file_times[eid] = ftime
        if not path.exists():
            index.issues.append(ParseIssue(eid, str(path), "missing_file", "listed in manifest but not on disk"))
            continue
        if path.stat().st_size == 0:
            index.issues.append(ParseIssue(eid, str(path), "unreadable", "empty file"))
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".sql":
                parse_sql(path, eid, ftime, index)
            elif suffix in (".json", ".jsonl"):
                text = path.read_text(encoding="utf-8")
                docs = [json.loads(l) for l in text.splitlines() if l.strip()] if suffix == ".jsonl" else [json.loads(text)]
                for doc in docs:
                    walk_json(doc, eid, ftime, index)
            elif suffix == ".log":
                parse_log(path, eid, ftime, index)
            elif suffix in (".txt", ".md", ".csv"):
                parse_text(path, eid, ftime, index)
            elif suffix == ".pdf":
                document_mentions(pdf_text(path), eid, ftime, index, "pdf", aliases)
            elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
                if vision is None:
                    index.issues.append(ParseIssue(eid, str(path), "unsupported", "no vision extractor configured"))
                    continue
                text = vision(path, eid)
                if text is None:
                    index.issues.append(ParseIssue(eid, str(path), "model_error", "vision extraction failed"))
                    continue
                document_mentions(text, eid, ftime, index, "image", aliases)
            else:
                index.issues.append(ParseIssue(eid, str(path), "unsupported", f"unsupported suffix {suffix}"))
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            index.issues.append(ParseIssue(eid, str(path), "unreadable", f"{type(exc).__name__}: {exc}"))
    index.observations.sort(key=Observation.sort_key)
    return index
