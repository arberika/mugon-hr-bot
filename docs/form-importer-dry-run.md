# Form-to-CRM planner: offline delivery and adapter contract

Status: tested pure planning layer, NOT a working deployed importer. It is not wired into the bot, webhook, scheduler, Google APIs or CRM. No live participants were read, imported, updated, merged, contacted or promoted. No credentials, OAuth grants or permissions were created. Do not close the Form importer Asana task based on this module.

## Existing source investigation

The current MUGON bot source and onboarding integration tree contain the existing `amocrm.py` adapter, with phone-oriented lead lookup. No Form/Sheet importer was found in those trees or the GitHub source-sheet-ID search. This bounded search does not prove that no implementation exists elsewhere. This addition belongs in the existing `arberika/mugon-hr-bot` repository; it is not a new service or a second CRM system. Existing bot files remain unchanged.

Source task: https://app.asana.com/1/1155245229582859/task/1216572224416570

Source Sheet: https://docs.google.com/spreadsheets/d/1FOGtmVOLnIAmhvy1NR6sNo3SPHcfK_d47o_WCZpP7SU/edit#gid=1504003644

Only metadata and A1:U1 headers were verified on 2026-10-19. Tab title: `Ответы на форму (1)`. A:O is the verified schema. 102 allocated rows are NOT a count of submitted responses. Actual affirmative consent option text remains unverified. `consent_values` has no default; production use must supply exact reviewed affirmative option text. Synthetic consent text in tests must never be used as production configuration.

## Entry points

- `validate_headers(headers)`: pin all 15 headers, reject renamed, reordered or additional nonempty headers; allow trailing empty grid columns.
- `plan_submission(Submission(...), headers=..., consent_values=..., records=..., receipts=..., snapshot_complete=...)`: private, in-memory proposal only.
- `plan_batch(submissions, **configuration)`: same inputs, with duplicate retry and batch collision gates. Prefer this over independent per-row calls for a batch.
- `audit_summary(plans)`: aggregate counts only. Safe default audit surface.

`Submission.cells` is the raw A:O value sequence (optional trailing blanks), with strings only. `self_submitted` is an explicit trusted ingestion assertion, not a participant-supplied spreadsheet field. The source must really be a Form submission from the participant, not a prospect/customer list copied into the response sheet. Merely being in the sheet does not establish that fact. The source Sheet and tab are allowlisted.

`submission_id` must be a stable trusted Form response identifier or a persisted ingestion identifier tied to the original response. A row number is not stable after insertion/sorting. This response Sheet header set has no verified response-ID column. An adapter must resolve this before using receipt-based retries. Do not silently invent identity from name, timestamp alone, or row position. If stable ID cannot be obtained through the already-authorized source, stop at schema validation and report this blocker; do not create a new endpoint, credentials or access.

`Record` is a complete, authorized CRM identity snapshot: stable record ID, all known emails, Telegram username and normalized symbolic stage. A future adapter must map CRM stage IDs against a verified live pipeline; numeric IDs from an old Amo instance must not be assumed current. `snapshot_complete=False` is the fail-closed default. Empty `records` with `snapshot_complete=True` means a genuinely verified empty identity population, not a failed/partial/paginated lookup. Bot's current phone-only `find_or_create_lead` is NOT a substitute for this identity lookup.

`Receipt` is a successful-application record in a durable ledger keyed by the planner's opaque source key, holding the fingerprint and actual target CRM ID. Only a confirmed write/readback may produce one. A generated dry-run plan is never an applied receipt. Same response and fingerprint with a valid receipt is a no-op; changed response requires review. This module does not implement persistent storage, locks or writes. Identity-level uniqueness/locking, crash recovery and reconciliation are therefore remaining live-execution work, not demonstrated by offline tests.

## Mapping and stage rules

- B/C: collected and self-entered email. Normalize whitespace/case; conflicting nonempty values require review. No provider-specific alias or dot normalization.
- E: exact Telegram username or t.me/telegram.me profile URL; reject invite links, message paths, foreign hosts and query-bearing links.
- K: unverified evidence text/links, never fetched. Nonempty evidence signals review only, even if it claims verification.
- L: preserve availability/work-format text, do not guess numeric hours or enum values.
- O: exact configured affirmative consent only; no generic yes/TRUE heuristic.
- PROFILE_FILLED is a *proposal* only when D/F/G/H/I/J/L/M/N and at least one identity are present. H remains explicitly self-reported. This does not validate skill or claim an interview occurred.
- For existing records, PROFILE_FILLED is proposed only from explicitly early symbolic stages NEW/JOINED/PROFILE_STARTED. Advanced, unknown and already-filled stages are preserved.
- The planner never proposes SKILL_VERIFICATION or VERIFIED. A separate authorized human review workflow and actual evidence validation are required for those stages.
- Conflicting/ambiguous CRM matches never merge. Different responses sharing an email, Telegram or target in a batch are all quarantined. Identical same-response retries generate one proposal; conflicting or revoked versions of the same response are quarantined.

## Later read-only dry-run using existing connector

1. Obtain authorization for the bounded participant-data read; this delivery did not read response rows. Use the already-connected Google Drive/Sheets connector, exact verified Sheet and tab, and the smallest explicit range. Do not retry denied DWD and do not create OAuth/keys.
2. Re-read headers and validate before interpreting values. Confirm exact affirmative consent text from authorized form configuration; confirm self-submission provenance and stable response IDs. If these cannot be verified, no production-row plan can be marked eligible.
3. Obtain a complete read-only exact-match CRM identity snapshot through the existing authorized CRM connector. Verify target account/pipeline and all pagination. Do not use the old Amo fallback or create records on lookup error.
4. Adapt the connector responses to the in-memory `Submission`, `Record` and existing successful `Receipt` types. Call `plan_batch`; no endpoint, job, webhook or credential configuration is needed for an interactive read-only dry-run.
5. Report `audit_summary` counts. Review details only in an appropriately private, authorized channel. Do not log/serialize submissions, private `Plan.fields`, keys/hashes, IDs, Telegram, email, names or evidence URLs to CI/public artifacts. No output files with participant values are needed.
6. Stop. Proposed changes are not executed. Live import requires separately authorized runtime wiring and CRM field mapping, conflict/staleness policy, durable receipts with identity locking, success readback, recovery tests and controlled rollout. Interactive connector access does not establish deployed runtime access.

## Offline acceptance

Run `python -m unittest discover -s tests -p 'test_form_importer.py' -v` and `python -m py_compile form_importer.py tests/test_form_importer.py`.

All fixture identities are invented and use `example.invalid`. Tests cover exact schema, consent/provenance/source gates, malformed inputs, B/C conflict, Telegram URL normalization, full identity-snapshot requirement, exact matches and ambiguity, partial profile/stage preservation, unverified evidence, receipt retries, changed responses, batch collisions/revoked consent and PII-free aggregate output. Import dependencies are restricted to standard-library pure computation (URL parsing only, no URL requests).

These tests do not establish CRM field mapping, actual Google/CRM runtime access, concurrency safety, persistent idempotency, integration deployment or end-to-end live success.
