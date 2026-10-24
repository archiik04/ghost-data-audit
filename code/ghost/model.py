"""Typed internal data structures shared by every pipeline stage."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------- time


def parse_time(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamps into aware UTC datetimes; None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------- identifiers

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d\s().\-]{6,}\d")
RESOURCE_RE = re.compile(
    r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|"
    r"tombstone|marketing|object|legal_hold|cache|email_delivery|telemetry)_\d+\b"
)

# Resource-id prefix -> owning system. Longest prefixes first.
PREFIX_SYSTEM = [
    ("audit_record_", "security_service"),
    ("data_flow_", "integration_service"),
    ("legal_hold_", "legal_service"),
    ("email_delivery_", "email_delivery_service"),
    ("account_", "account_service"),
    ("search_", "search_service"),
    ("analytics_", "analytics_platform"),
    ("support_", "support_platform"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
    ("tombstone_", "identity_service"),
    ("marketing_", "marketing_platform"),
    ("object_", "object_storage"),
    ("cache_", "cache_service"),
    ("telemetry_", "telemetry_service"),
]

NULLISH = {"", "null", "none", "nil", "[redacted]", "redacted", "n/a", "-", "no direct identifier"}


def system_for(resource_id: str) -> Optional[str]:
    for prefix, system in PREFIX_SYSTEM:
        if resource_id.startswith(prefix):
            return system
    return None


def is_resource_id(value: str) -> bool:
    return bool(RESOURCE_RE.fullmatch(value or ""))


def norm_email(value: str) -> str:
    return value.strip().lower()


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def norm_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z ]", "", text.lower().replace("-", " ")).strip()


def is_nullish(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULLISH)


def classify_identifier(value) -> Optional[tuple[str, str]]:
    """Return (kind, normalized) for a single identifier-like value.

    Kinds: email, hash, user, phone. Names are handled separately because they
    are never sufficient on their own to establish ownership.
    """
    if not isinstance(value, str) or is_nullish(value):
        return None
    text = value.strip().strip('"')
    if EMAIL_RE.fullmatch(text):
        return ("email", norm_email(text))
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return ("hash", text.lower())
    if re.fullmatch(r"user_\d+", text):
        return ("user", text)
    if re.fullmatch(r"\+?[\d\s().\-]{8,}", text) and len(norm_phone(text)) >= 8:
        return ("phone", norm_phone(text))
    return None


def identifiers_in_text(text: str) -> set[tuple[str, str]]:
    """Identifiers embedded in free text (messages, PDFs, OCR output)."""
    found: set[tuple[str, str]] = set()
    if not text:
        return found
    for m in EMAIL_RE.findall(text):
        found.add(("email", norm_email(m)))
    for m in HASH_RE.findall(text.lower()):
        found.add(("hash", m))
    for m in USER_RE.findall(text):
        found.add(("user", m))
    for m in PHONE_RE.findall(text):
        digits = norm_phone(m)
        if len(digits) >= 8:
            found.add(("phone", digits))
    return found


# ---------------------------------------------------------------- observations


@dataclass
class Observation:
    """One fact about one resource, extracted from one evidence item.

    ``present``: True = record exists, False = verified absent, None = unknown.
    ``exposed``: direct identifiers (kind, value) still visible in the record.
    ``keys``: identifiers that tie the record to a subject (lookup keys,
    references, hashes). Exposed identifiers are always keys as well.
    """

    evidence_id: str
    time: datetime
    resource_id: str
    system: str
    source: str  # parser label, e.g. "sql", "json", "log", "ledger", "pdf", "image", "activity"
    present: Optional[bool] = None
    exposed: set = field(default_factory=set)
    keys: set = field(default_factory=set)
    names: set = field(default_factory=set)
    stable_ref: Optional[str] = None  # user_id kept on the record itself
    enabled: Optional[bool] = None  # data flows
    restore_allowed: Optional[bool] = None  # backups
    record_type: Optional[str] = None
    result: Optional[str] = None
    note: str = ""
    replica: Optional[str] = None
    shared_context: bool = False  # support record also concerns other parties
    retain_until: Optional[datetime] = None
    linked_resources: set = field(default_factory=set)  # e.g. search doc -> source account
    pii_checked: bool = False  # the record states whether direct identifiers are present
    event: str = ""

    def sort_key(self):
        return (self.time, self.evidence_id, self.source, self.resource_id)


@dataclass
class Coverage:
    """A full dump of one system at one time: absence of a known resource is meaningful."""

    evidence_id: str
    time: datetime
    system: str
    resources: set
    label: str = ""


@dataclass
class ParseIssue:
    evidence_id: str
    path: str
    category: str  # missing_file | unreadable | unsupported | partial | model_error
    detail: str


@dataclass
class EvidenceIndex:
    observations: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    file_times: dict = field(default_factory=dict)

    def by_resource(self) -> dict:
        out: dict = {}
        for obs in self.observations:
            out.setdefault(obs.resource_id, []).append(obs)
        for items in out.values():
            items.sort(key=Observation.sort_key)
        return out
