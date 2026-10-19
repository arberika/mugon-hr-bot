"""Only invented people/identities and no external IO."""
import json
from dataclasses import replace
import unittest

from form_importer import (HEADERS, SOURCE_SHEET, Submission, Record, Receipt,
                           audit_summary, normalize_telegram, plan_batch, plan_submission,
                           validate_headers)

CONSENT = frozenset({"SYNTHETIC explicit consent"})
CELLS = ("2026-10-19 10:00:00", "invented@example.invalid", "invented@example.invalid",
         "Synthetic Person", "@fictional_user", "UTC", "Developer", "Self-reported middle",
         "Python", "Synthetic project, built fixture, passed tests", "https://example.invalid/demo",
         "2 hours/week, volunteer", "Member", "Learn and contribute", next(iter(CONSENT)))


def submission(**changes):
    return replace(Submission("synthetic-form-response-1", CELLS, True), **changes)


def changed(index, value):
    row = list(CELLS)
    row[index] = value
    return submission(cells=tuple(row))


def plan(row=None, **kwargs):
    return plan_submission(row or submission(), headers=HEADERS, consent_values=CONSENT,
                           snapshot_complete=kwargs.pop("snapshot_complete", True), **kwargs)


class PlannerTests(unittest.TestCase):
    def test_exact_schema_and_blank_allocated_columns(self):
        validate_headers((*HEADERS, "", " "))

    def test_reordered_headers_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema_mismatch"):
            validate_headers((HEADERS[1], HEADERS[0], *HEADERS[2:]))

    def test_extra_named_header_rejected(self):
        with self.assertRaises(ValueError):
            validate_headers((*HEADERS, "unexpected"))

    def test_consent_configuration_mandatory(self):
        with self.assertRaisesRegex(ValueError, "consent_configuration"):
            plan_submission(submission(), headers=HEADERS, consent_values=frozenset())

    def test_no_self_submission(self):
        self.assertEqual(plan(submission(self_submitted=False)).reason, "not_self_submitted")
        self.assertEqual(plan(submission(self_submitted=1)).reason, "not_self_submitted")

    def test_no_inferred_consent(self):
        for value in ("", "yes", "no", "TRUE", "I do not consent"):
            self.assertEqual(plan(changed(14, value)).action, "skip")

    def test_wrong_source_quarantined(self):
        self.assertEqual(plan(submission(sheet_id="other")).reason, "untrusted_source")
        self.assertEqual(plan(submission(tab_id="other")).reason, "untrusted_source")

    def test_missing_stable_id_and_timestamp(self):
        self.assertEqual(plan(submission(submission_id="")).action, "review")
        self.assertEqual(plan(changed(0, "")).reason, "missing_timestamp")

    def test_short_extra_and_nonstring_row(self):
        for cells in (CELLS[:14], (*CELLS, "unexpected"), (*CELLS[:3], None, *CELLS[4:])):
            self.assertEqual(plan(submission(cells=cells)).action, "review")

    def test_complete_profile_proposal(self):
        p = plan()
        self.assertEqual(p.action, "propose_create")
        self.assertEqual(p.proposed_stage, "PROFILE_FILLED")
        self.assertEqual(p.fields["availability"], CELLS[11])
        self.assertEqual(p.fields["evidence"], CELLS[10])
        self.assertTrue(p.evidence_review_required)
        self.assertNotIn("consent", p.fields)
        self.assertNotIn("collected_email", p.fields)

    def test_email_conflict(self):
        self.assertEqual(plan(changed(2, "other@example.invalid")).reason, "email_conflict")

    def test_email_canonicalization_and_single_column(self):
        self.assertEqual(plan(changed(2, " INVENTED@EXAMPLE.INVALID ")).action, "propose_create")
        self.assertEqual(plan(changed(2, "")).fields["email"], CELLS[1])
        self.assertEqual(plan(changed(1, "")).fields["email"], CELLS[2])

    def test_invalid_email(self):
        self.assertEqual(plan(changed(2, "bad")).reason, "invalid_email")

    def test_telegram_normalization(self):
        for value in ("@Fictional_User", "fictional_user", "https://t.me/Fictional_User/", "telegram.me/fictional_user"):
            self.assertEqual(normalize_telegram(value), "fictional_user")

    def test_telegram_urls_not_arbitrary_identity(self):
        for value in ("https://evil.invalid/fictional_user", "https://t.me/+invite", "https://t.me/fictional_user/1",
                      "https://t.me/fictional_user?start=foo", "@abc", "https://t.me/fictional_user#x"):
            with self.assertRaises(ValueError):
                normalize_telegram(value)

    def test_no_identity(self):
        cells = list(CELLS)
        for index in (1, 2, 4):
            cells[index] = ""
        self.assertEqual(plan(submission(cells=tuple(cells))).reason, "missing_identity")

    def test_incomplete_snapshot_blocks_creation(self):
        self.assertEqual(plan(snapshot_complete=False).reason, "incomplete_identity_snapshot")

    def test_email_and_telegram_search_existing(self):
        for r in (Record("fake-1", (CELLS[1],), stage="JOINED"),
                  Record("fake-1", telegram="fictional_user", stage="JOINED")):
            p = plan(records=[r])
            self.assertEqual(p.action, "propose_update")
            self.assertEqual(p.target_id, "fake-1")
            self.assertEqual(p.proposed_stage, "PROFILE_FILLED")

    def test_different_identity_matches_quarantined(self):
        p = plan(records=[Record("fake-1", (CELLS[1],)), Record("fake-2", telegram="fictional_user")])
        self.assertEqual(p.reason, "ambiguous_identity")

    def test_duplicate_existing_email_quarantined(self):
        p = plan(records=[Record("fake-1", (CELLS[1],)), Record("fake-2", (CELLS[1],))])
        self.assertEqual(p.reason, "ambiguous_identity")

    def test_conflicting_existing_identity_not_overwritten(self):
        for r in (Record("fake-1", ("other@example.invalid",), "fictional_user"),
                  Record("fake-1", (CELLS[1],), "other_user")):
            self.assertEqual(plan(records=[r]).reason, "conflicting_existing_identity")

    def test_invalid_snapshot(self):
        for records in ([Record("fake-1", ("bad",))], [Record("fake-1"), Record("fake-1")], [Record("")]):
            self.assertEqual(plan(records=records).reason, "invalid_identity_snapshot")

    def test_partial_never_profile_filled(self):
        for index in (3, 5, 6, 7, 8, 9, 11, 12, 13):
            self.assertIsNone(plan(changed(index, "")).proposed_stage)

    def test_advanced_or_unknown_stage_never_overwritten(self):
        for stage in ("", "UNKNOWN", "PROFILE_FILLED", "SKILL_VERIFICATION", "VERIFIED", "ACTIVE"):
            self.assertIsNone(plan(records=[Record("fake-1", (CELLS[1],), stage=stage)]).proposed_stage)

    def test_evidence_only_flags_review_never_verifies(self):
        for evidence in ("", "https://example.invalid/demo", "I am verified"):
            p = plan(changed(10, evidence))
            self.assertNotIn(p.proposed_stage, {"VERIFIED", "SKILL_VERIFICATION"})
            self.assertEqual(p.evidence_review_required, bool(evidence))

    def test_applied_receipt_makes_retry_noop(self):
        p = plan()
        self.assertEqual(plan(receipts={p.key: Receipt(p.fingerprint, "fake-1")}).reason, "already_applied")

    def test_changed_submission_or_invalid_receipt_needs_review(self):
        p = plan()
        self.assertEqual(plan(changed(11, "3 hours/week"), receipts={p.key: Receipt(p.fingerprint, "fake-1")}).action, "review")
        self.assertEqual(plan(receipts={p.key: Receipt(p.fingerprint, "")}).action, "review")

    def test_pii_free_summary_and_repr(self):
        p = plan()
        text = json.dumps(audit_summary([p])) + repr(p)
        for private in ("invented", "fictional_user", "Synthetic Person", p.key, p.fingerprint):
            self.assertNotIn(private, text)

    def batch(self, rows):
        return plan_batch(rows, headers=HEADERS, consent_values=CONSENT, snapshot_complete=True)

    def test_batch_identical_retry_one_proposal(self):
        self.assertEqual([p.action for p in self.batch([submission(), submission()])], ["propose_create", "skip"])

    def test_batch_different_response_same_identity_all_review(self):
        self.assertEqual([p.action for p in self.batch([submission(), submission(submission_id="other")])], ["review", "review"])

    def test_batch_changed_response_same_id_all_review(self):
        self.assertEqual([p.action for p in self.batch([submission(), changed(11, "3 hours/week")])], ["review", "review"])

    def test_batch_duplicate_of_collision_also_review(self):
        self.assertEqual([p.action for p in self.batch([submission(), submission(), submission(submission_id="other")])], ["review"] * 3)

    def test_batch_revoked_consent_same_response_blocks_earlier_proposal(self):
        for rows in ([submission(), changed(14, "no")], [changed(14, "no"), submission()]):
            self.assertEqual([p.action for p in self.batch(rows)], ["review", "review"])

    def test_batch_invalid_version_blocks_earlier_proposal(self):
        for invalid in (changed(0, ""), changed(2, "bad"), changed(4, "bad")):
            for rows in ([submission(), invalid], [invalid, submission()]):
                self.assertEqual([p.action for p in self.batch(rows)], ["review", "review"])

    def test_batch_same_response_changed_provenance_blocks_proposal(self):
        self.assertEqual([p.action for p in self.batch([submission(), submission(self_submitted=False)])], ["review", "review"])

    def test_no_network_or_runtime_adapter_dependency(self):
        import ast
        from pathlib import Path
        source = Path(__file__).resolve().parents[1] / "form_importer.py"
        tree = ast.parse(source.read_text())
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules.update(a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names)
        self.assertLessEqual(modules, {"__future__", "collections", "dataclasses", "hashlib", "json", "re", "typing", "urllib.parse"})

    def test_batch_distinct_people(self):
        cells = list(CELLS)
        cells[1] = cells[2] = "another@example.invalid"
        cells[4] = "@another_fake"
        self.assertEqual([p.action for p in self.batch([submission(), submission(submission_id="other", cells=tuple(cells))])], ["propose_create"] * 2)


if __name__ == "__main__":
    unittest.main()
