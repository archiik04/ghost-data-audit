"""Policy: map a resource to its data category and the action deletion_rules.csv requires."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DELETE, REDACT, ANONYMIZE, RETAIN = "DELETE", "REDACT", "ANONYMIZE", "RETAIN"
DISABLE, BLOCK = "DISABLE", "BLOCK"  # process controls (flows / restore paths)

SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "cache_service": "CACHE_ENTRY",
    "support_platform": "SUPPORT_MESSAGE",
    "analytics_platform": "ANALYTICS_PROFILE",
    "telemetry_service": "PRODUCT_TELEMETRY",
    "export_service": "USER_EXPORT",
    "marketing_platform": "MARKETING_PROFILE",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "security_service": "SECURITY_AUDIT_LOG",
    "legal_service": "LEGAL_HOLD_RECORD",
    "backup_service": "BACKUP_ARCHIVE",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "object_storage": "SUPPORT_ATTACHMENT",
    "integration_service": "DATA_FLOW",
}

WORKFLOW_ACTION = {"DELETE_RECORD": DELETE, "REDACT_RECORD": REDACT, "ANONYMIZE_RECORD": ANONYMIZE}


@dataclass
class Decision:
    category: str
    action: str
    rule_id: str
    reason: str


class Policy:
    def __init__(self, rules: list[dict]):
        self.rules: dict[str, dict] = {}
        for r in rules:
            cat = r["data_category"].strip().upper()
            if cat in self.rules:
                # more than one rule per category: keep the most protective (retain > redact/anonymize > delete)
                order = {RETAIN: 3, REDACT: 2, ANONYMIZE: 2, DELETE: 1}
                if order.get(r["required_action"].strip().upper(), 0) <= order.get(self.rules[cat]["required_action"], 0):
                    continue
            self.rules[cat] = {**r, "required_action": r["required_action"].strip().upper()}

    def decide(self, system: str, record_type: Optional[str], workflow_actions: set,
               shared_context: Optional[bool]) -> Decision:
        if system == "integration_service":
            return Decision("DATA_FLOW", DISABLE, "-", "active flows can recreate deleted data")
        category = (record_type or "").upper() if (record_type or "").upper() in self.rules else SYSTEM_CATEGORY.get(system, "")
        reason = f"system {system}"
        if system == "support_platform" and category != "SUPPORT_ATTACHMENT":
            # Rule 5 only applies when the conversation carries shared context; otherwise the
            # message belongs to the subject alone and is deleted. The workflow's own
            # classification of the record (its attempted action) is the best available signal.
            if shared_context is False or (shared_context is None and workflow_actions == {DELETE}):
                category, reason = "SUPPORT_ATTACHMENT" if record_type == "SUPPORT_ATTACHMENT" else "SUPPORT_MESSAGE_PRIVATE", \
                    "support record without shared context"
        if system == "analytics_platform" and workflow_actions == {DELETE}:
            category, reason = "MARKETING_PROFILE", "analytics contact record classified for deletion"
        if record_type == "MARKETING_CONTACT":
            category, reason = "MARKETING_PROFILE", "marketing contact (email opt-in) record"
        rule = self.rules.get(category)
        if category == "SUPPORT_MESSAGE_PRIVATE":
            return Decision(category, DELETE, self.rules.get("SUPPORT_ATTACHMENT", {}).get("rule_id", "-"), reason)
        if rule is None:
            return Decision(category or "UNKNOWN", DELETE, "-", "no matching rule: personal data defaults to deletion")
        action = rule["required_action"]
        if category == "BACKUP_ARCHIVE":
            action = BLOCK
        return Decision(category, action, rule["rule_id"], reason)
