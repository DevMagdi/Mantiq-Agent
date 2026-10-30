# Phase 3 — multi-channel communication, WhatsApp and AI voice

Branch: `arena/40666a92-mantiq-agent` (Arena binds the session to this branch, so the branch named
`phase-3-channels-voice-whatsapp` in the specification cannot be created — everything below is on the
session branch and merges to `main` from here).

This document is the evidence pack for the PR. Every block is real terminal output from the commit
being merged; nothing here is projected or "expected".

---

## 1. What was built

| Spec item | Delivered |
| --- | --- |
| 1.1 Channel selection | `Supplier.preferred_channel` / `secondary_channels` / `preferred_language`, `RFQSupplier.channel_override` + `channel_source`, one resolver (`services/channels.resolve`) with the order **RFQ override → supplier preferred → supplier secondary → whatsapp, voice, email, web_form**, per-candidate reasons, and the active channel shown per supplier on the RFQ screen and in a supplier channel panel |
| 1.2 WhatsApp (first-class) | `WhatsAppProvider` (text, approved templates, documents, interactive) with a documented Cloud API client and a deterministic fake; inbound text / audio (STT) / documents (OCR) → the existing extraction + verification pipeline; delivery and read receipts that never move backwards; consent (STOP/START, AR+EN) honoured before parsing; 10 bilingual WhatsApp templates |
| 1.3 Voice | Outbound human and AI calls linked to RFQ + supplier, disclosure first, recording only on explicit consent, country calling windows, full transcript and per-turn audit |
| 1.4 Email | Templates, threading and a real fallback when WhatsApp is unavailable — the resolver explains the switch instead of silently changing channel |
| 1.5 Unified inbox | `GET /inbox/conversations`, `GET /inbox/threads/{id}`, `POST /inbox/threads/{id}/reply`, `GET /inbox/search`; one thread per (RFQ, supplier) with every channel listed and the reply landing on the channel the supplier used |
| 2.1 AI voice agent | Deterministic, policy-first: approved-data answers only, hard blocks on competitor disclosure / commitments / award intent / spec change / payment execution, `block_code` per turn, transcript → the normal verification queue |
| 2.2 Internal assistant | Read-only assistant for requester / buyer / manager / warehouse / finance: PR drafting (the only write, and it stays unsubmitted), P2P status, offer and recommendation summaries, outstanding balances, supplier communication history; intent→permission map, ownership checks, forbidden-verb refusal, every turn stored and audited |
| 3 Templates, reminders, statuses | 20 bilingual template rows per tenant with `kind`, description and extracted variables; reminders and missing-information follow-ups resolve the supplier's channel and language; comm status sent → delivered → read → replied / no response; channel switch mid-conversation with history preserved; consent and opt-out per channel |
| 4 Technical | Migration `0016_channels_voice` (5 new tables/columns sets, RLS, `REVOKE UPDATE, DELETE` on the three evidence tables, 14 inline templates, grants for 7 roles); deployment switch **and** per-tenant flag with a single reader (`services/features.py`); new permissions `channel:read/write`, `inbox:read/reply`, `voice:call`, `assistant:use`; full AR-RTL + EN on every new screen |

New API surface: `channels/capabilities`, per-supplier and per-RFQ channel endpoints, WhatsApp send /
quote request / receipts, inbox endpoints, voice call console, template library, assistant ask and
transcribe, feature-flag admin. `docs/API.md` was regenerated from the live OpenAPI document (178 paths
/ 207 operations) and gained a phase-3 section with request/response examples.

---

## 2. Backend gates

```text
$ cd backend && /home/user/venv/bin/python -m pytest tests
........................................................................ [ 76%]
........................................................................ [ 87%]
........................................................................ [ 98%]
...........                                                              [100%]
659 passed in 44.87s
```

Phase-3 modules only (the other 583 tests are the phase-1/2 suite and were not touched):

```text
$ /home/user/venv/bin/python -m pytest tests/test_services_channels.py tests/test_services_whatsapp.py \
    tests/test_services_voice_agent.py tests/test_services_assistant.py tests/test_services_inbox.py \
    tests/test_api_channels.py tests/test_services_reminders_templates.py
....                                                                     [100%]
76 passed in 7.16s
```

Per module: channels 12, WhatsApp 11, AI voice agent 9, assistant 11, inbox 7, HTTP surface 18,
templates & reminders 8.

```text
$ /home/user/venv/bin/python -m ruff check app tests
All checks passed!
```

## 3. Migration

Fresh SQLite database, full chain, real output:

```text
$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0010_rls_policies_and_grants -> 0011_indexes_search_vectors ...
INFO  [alembic.runtime.migration] Running upgrade 0011_indexes_search_vectors -> 0012_bootstrap_reference_data ...
INFO  [alembic.runtime.migration] Running upgrade 0012_bootstrap_reference_data -> 0013_post_po_lifecycle ...
INFO  [alembic.runtime.migration] Running upgrade 0013_post_po_lifecycle -> 0014_drop_theme_preference ...
INFO  [alembic.runtime.migration] Running upgrade 0014_drop_theme_preference -> 0015_accounting ...
INFO  [alembic.runtime.migration] Running upgrade 0015_accounting -> 0016_channels_voice, channels & voice: WhatsApp-first preferences, delivery evidence, AI call turns, assistant audit

$ alembic check
No new upgrade operations detected.
```

```text
$ python -m app.cli bootstrap      # then, reading the same database back
tenants (2,)
templates [('email', 'ar', 8), ('email', 'en', 6), ('voice', 'ar', 2), ('voice', 'en', 2),
           ('whatsapp', 'ar', 12), ('whatsapp', 'en', 10)]
distinct template kinds: 6        # 20 template rows per tenant
new tables present: channel_event, voice_call_turn, assistant_session, assistant_turn
channel_message columns: 27
```

PostgreSQL-only verification (RLS, `REVOKE UPDATE/DELETE` on `channel_event`, `voice_call_turn`,
`assistant_turn`) runs in the CI job `postgres` through `scripts/verify_rls.py`, which now performs
29 guarantee checks (6 original + 13 phase-2 + 10 phase-3). It cannot run in this sandbox: there is no
PostgreSQL with `pgcrypto`/`vector` here, and SQLite has no roles or policies.

## 4. Frontend gates

```text
$ npx tsc -b
TSC_EXIT=0

$ npx eslint src --max-warnings 0
ESLINT_EXIT=0

$ npx vitest run
 ✓ src/lib/format.test.ts (10 tests) 22ms
 ✓ src/i18n/i18n.test.ts (3 tests) 4ms

 Test Files  13 passed (13)
      Tests  74 passed (74)
   Start at  23:33:42
   Duration  13.18s

$ npm run build
dist/assets/index-Ds41N1eW.css    78.72 kB │ gzip:  20.35 kB
dist/assets/charts-1jBeNFW4.js   407.41 kB │ gzip: 110.02 kB
dist/assets/index-DOzaCL7Y.js    528.71 kB │ gzip: 137.33 kB
✓ built in 6.83s
```

New screens: Inbox (conversation list + thread + reply + channel switch), Voice console (call list,
transcript, policy turns, extraction), Template library (AR/EN, variables, edit), Assistant (ask,
transcribe, session history, `can_act: false`), the RFQ channel panel (per-supplier channel, override
with reason, comm status) and the supplier channel panel. The settings screen gained a `channels` tab
that toggles the tenant feature flags and explains when a flag is off at the deployment level.

`frontend/src/pages/channels.test.tsx` (8 tests) covers: inbox rendering and reply, channel switch
messaging, voice console transcript and blocked turns, template library, assistant refusal badges, and
the feature-flag tab.

## 5. Safety evidence (unchanged invariants, phase-3 additions)

* **No autonomous commitment.** `voice_agent` returns a fixed refusal plus a `block_code`
  (`COMPETITOR_DISCLOSURE`, `ACCEPT_INTENT`, `SPEC_CHANGE`, `SIGN_INTENT`) for blocked topics, and its
  answers come from `approved-data-v1` only. No branch creates an approved offer: the transcript goes
  to `extraction`, exactly like an email reply.
* **The assistant cannot act.** `assistant.ask` always returns `can_act: false`; approve/award/sign/pay/
  post phrases classify as `forbidden` (`ASSISTANT_FORBIDDEN_ACTION`); the only write is `pr_create`,
  which leaves a `clarifying` requisition the user submits themselves.
* **Existing forbidden-tool assertions untouched.** `tests/test_agents_graph.py::test_post_award_human_actions_have_no_agent_tool`
  still passes inside the 659.
* **Consent.** `STOP`/`START` (AR + EN) on any channel is handled before the message is parsed, and the
  event is stored with the supplier's own words; recordings are dropped when consent is refused.
* **Evidence is append-only** at the database level (migration `0016`, checked by `verify_rls.py`).

## 6. Honest limits (also in `docs/PROGRESS.md`)

* WhatsApp is proven against the deterministic fake provider; a live Meta/BSP account is required for
  production traffic. The Cloud API request/response shapes are implemented.
* Voice has no live audio bridge: the AI agent plays a scripted transcript through the telephony seam
  and stores every turn. There is no TTS synthesis and no live STT stream (STT exists for recordings
  and for the assistant's `/assistant/transcribe` endpoint).
* The assistant and the AI voice agent are deliberately deterministic (rules, not an LLM) so the
  guardrails cannot be prompt-injected. `docs/DECISIONS.md` #82–#88 explains the trade-off.
* No browser binary in the sandbox: the UI is verified by typecheck, ESLint, 74 jsdom tests and a
  production build, not by Playwright.

## 7. Decisions added in this phase

`docs/DECISIONS.md` #71–#88: single resolver, override needs a reason, per-channel consent, WhatsApp
as a transport with a real contract, monotonic receipts, threads per (RFQ, supplier), consent refusals
as stored verdicts, append-only evidence, reminders follow the resolved channel, template kernel, two
switches per capability, deterministic policy-first voice agent, disclosure/consent/per-turn audit,
transcript → verification queue, read-only assistant, ownership after permission, speech as untrusted
input.
