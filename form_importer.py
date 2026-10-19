"""Offline Form-to-CRM planning only. No network, credentials, writes, or stage changes.

A future authorized adapter must supply a complete CRM identity snapshot and a
persisted receipt ledger; a plan is never evidence that a CRM write succeeded.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

SOURCE_SHEET = "1FOGtmVOLnIAmhvy1NR6sNo3SPHcfK_d47o_WCZpP7SU"
SOURCE_TAB = "1504003644"
HEADERS = (
    "Отметка времени", "Адрес электронной почты", "Ваш email", "Имя и фамилия",
    "Telegram username или ссылка на профиль", "Страна, город и часовой пояс",
    "Ваша основная роль", "Ваш уровень", "Технологии, с которыми вы реально работаете сейчас",
    "Один лучший проект: опишите вашу роль, основную задачу и измеримый результат",
    "Ссылки на доказательства навыков: GitHub, LinkedIn, портфолио или demo",
    "Доступность: сколько часов в неделю готовы уделять и какой желаемый формат участия",
    "Какую роль вы готовы попробовать в клубе",
    "Что вы можете дать клубу и что хотите получить от участия", "Согласие на обработку данных",
)
FIELD_NAMES = ("timestamp", "collected_email", "email", "name", "telegram", "location_timezone",
               "role", "self_reported_level", "technologies", "project", "evidence",
               "availability", "club_role", "contribution", "consent")
REQUIRED_PROFILE = ("name", "location_timezone", "role", "self_reported_level", "technologies",
                    "project", "availability", "club_role", "contribution")
EARLY_STAGES = frozenset({"NEW", "JOINED", "PROFILE_STARTED"})


@dataclass(frozen=True)
class Record:
    """Already normalized CRM snapshot; exact matches only, never fuzzy merge."""
    record_id: str
    emails: tuple[str, ...] = ()
    telegram: str = ""
    stage: str = ""


@dataclass(frozen=True)
class Receipt:
    fingerprint: str
    record_id: str


@dataclass(frozen=True)
class Submission:
    # Stable adapter-issued form response ID, NOT mutable spreadsheet row position.
    submission_id: str
    cells: tuple[str, ...]
    self_submitted: bool = False
    sheet_id: str = SOURCE_SHEET
    tab_id: str = SOURCE_TAB


@dataclass(frozen=True)
class Plan:
    action: str  # skip / review / propose_create / propose_update
    reason: str
    key: str = field(default="", repr=False)
    fingerprint: str = field(default="", repr=False)
    target_id: str = field(default="", repr=False)
    fields: Mapping[str, str] = field(default_factory=dict, repr=False)
    proposed_stage: str | None = None
    evidence_review_required: bool = False


def validate_headers(headers: Sequence[str]) -> None:
    if tuple(headers[:15]) != HEADERS or any(str(v).strip() for v in headers[15:]):
        raise ValueError("schema_mismatch")


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("invalid_email")
    return value


def normalize_telegram(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("https://", "http://", "t.me/", "telegram.me/")):
        url = urlsplit(value if "://" in value else "https://" + value)
        if (url.netloc.lower() not in {"t.me", "telegram.me"} or url.query or url.fragment
                or url.username or url.password):
            raise ValueError("invalid_telegram")
        value = url.path.strip("/")
    value = value.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", value):
        raise ValueError("invalid_telegram")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_submission(submission: Submission, *, headers: Sequence[str],
                    consent_values: frozenset[str], records: Sequence[Record] = (),
                    receipts: Mapping[str, Receipt] | None = None,
                    snapshot_complete: bool = False) -> Plan:
    """Return a private proposal. Caller must not log fields, keys or target IDs.

    consent_values must contain exact approved affirmative Form choice text. No
    inferred consent, defaults, CRM creation, source writes, or stage promotion.
    """
    validate_headers(headers)
    if not consent_values or any(not v.strip() for v in consent_values):
        raise ValueError("consent_configuration_required")
    if (submission.sheet_id, submission.tab_id) != (SOURCE_SHEET, SOURCE_TAB):
        return Plan("review", "untrusted_source")
    if submission.self_submitted is not True:
        return Plan("skip", "not_self_submitted")
    if not submission.submission_id.strip():
        return Plan("review", "missing_submission_id")
    if len(submission.cells) < 15 or any(str(v).strip() for v in submission.cells[15:]):
        return Plan("review", "invalid_row_shape")
    if any(not isinstance(v, str) for v in submission.cells):
        return Plan("review", "invalid_cell_type")
    data = dict(zip(FIELD_NAMES, (v.strip() for v in submission.cells[:15])))
    if data["consent"] not in consent_values:
        return Plan("skip", "consent_missing_or_unapproved")
    if not data["timestamp"]:
        return Plan("review", "missing_timestamp")
    try:
        collected = normalize_email(data["collected_email"])
        entered = normalize_email(data["email"])
        telegram = normalize_telegram(data["telegram"])
    except ValueError as error:
        return Plan("review", str(error))
    if collected and entered and collected != entered:
        return Plan("review", "email_conflict")
    email = entered or collected
    if not email and not telegram:
        return Plan("review", "missing_identity")
    if not snapshot_complete:
        return Plan("review", "incomplete_identity_snapshot")
    key = _digest([submission.sheet_id, submission.tab_id, submission.submission_id])
    fingerprint = _digest(data)
    receipt = (receipts or {}).get(key)
    if receipt:
        if receipt.fingerprint == fingerprint and receipt.record_id:
            return Plan("skip", "already_applied", key=key, fingerprint=fingerprint)
        return Plan("review", "changed_or_invalid_receipt", key=key, fingerprint=fingerprint)
    matches: dict[str, Record] = {}
    try:
        seen_ids: set[str] = set()
        for record in records:
            if not record.record_id or record.record_id in seen_ids:
                return Plan("review", "invalid_identity_snapshot")
            seen_ids.add(record.record_id)
            emails = {normalize_email(e) for e in record.emails if e.strip()}
            tg = normalize_telegram(record.telegram)
            if (email and email in emails) or (telegram and telegram == tg):
                matches[record.record_id] = record
    except ValueError:
        return Plan("review", "invalid_identity_snapshot")
    if len(matches) > 1:
        return Plan("review", "ambiguous_identity")
    target = next(iter(matches.values()), None)
    if target:
        emails = {normalize_email(e) for e in target.emails if e.strip()}
        tg = normalize_telegram(target.telegram)
        if (email and emails and email not in emails) or (telegram and tg and telegram != tg):
            return Plan("review", "conflicting_existing_identity")
    fields = {k: v for k, v in data.items() if k not in {"collected_email", "consent", "timestamp"} and v}
    fields.update({k: v for k, v in {"email": email, "telegram": telegram}.items() if v})
    complete = all(data[k] for k in REQUIRED_PROFILE)
    # Unknown/advanced stages are never overwritten. Evidence is only a review cue.
    stage = "PROFILE_FILLED" if complete and (target is None or target.stage in EARLY_STAGES) else None
    return Plan("propose_update" if target else "propose_create", "profile_complete" if complete else "profile_partial",
                key, fingerprint, target.record_id if target else "", fields, stage, bool(data["evidence"]))


def audit_summary(plans: Iterable[Plan]) -> dict[str, object]:
    """Counts only. Does not disclose source IDs, identities, data or receipt hashes."""
    plans = list(plans)
    return {"total": len(plans), "actions": dict(Counter(p.action for p in plans)),
            "evidence_review_required": sum(p.evidence_review_required for p in plans)}


def plan_batch(submissions: Sequence[Submission], **configuration) -> list[Plan]:
    """Offline batch planning with in-batch duplicate and identity collision gates.

    Identical retries appear once. Different submissions sharing any identity are
    all quarantined for review, even when they might be legitimate repeat forms.
    """
    plans = [plan_submission(s, **configuration) for s in submissions]
    # An edited/revoked submission must also block an earlier eligible version.
    versions: dict[tuple[str, str, str], set[str]] = {}
    for row in submissions:
        identity = (row.sheet_id, row.tab_id, row.submission_id)
        versions.setdefault(identity, set()).add(_digest([row.cells, row.self_submitted]))
    changed_submission_indexes = {
        i for i, row in enumerate(submissions)
        if len(versions[(row.sheet_id, row.tab_id, row.submission_id)]) > 1
    }
    first: dict[str, int] = {}
    duplicate_indexes: set[int] = set()
    changed_keys: set[str] = set()
    for i, plan in enumerate(plans):
        if not plan.key or not plan.action.startswith("propose_"):
            continue
        if plan.key in first:
            other = plans[first[plan.key]]
            if plan.fingerprint == other.fingerprint:
                duplicate_indexes.add(i)
            else:
                changed_keys.add(plan.key)
        else:
            first[plan.key] = i
    identity_owners: dict[tuple[str, str], list[int]] = {}
    for i, plan in enumerate(plans):
        if not plan.action.startswith("propose_") or i in duplicate_indexes:
            continue
        for name in ("email", "telegram"):
            if plan.fields.get(name):
                identity_owners.setdefault((name, plan.fields[name]), []).append(i)
        if plan.target_id:
            identity_owners.setdefault(("target", plan.target_id), []).append(i)
    collisions = {i for owners in identity_owners.values() if len(owners) > 1 for i in owners}
    for i, plan in enumerate(plans):
        if i in changed_submission_indexes or plan.key in changed_keys or i in collisions:
            plans[i] = Plan("review", "batch_identity_conflict")
        elif i in duplicate_indexes:
            # If the original proposal was quarantined, the retry is quarantined too.
            original = first[plan.key]
            plans[i] = Plan("review", "batch_identity_conflict") if original in collisions else Plan("skip", "duplicate_in_batch")
    return plans
