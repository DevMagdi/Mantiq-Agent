# Agent design

ProcureAI's agent is a **bounded, audited workflow** — not an autonomous negotiator. Language
models produce text and proposals; a deterministic policy engine decides what may happen; a human
decides what is awarded.

```
requisition ─▶ orchestrator graph ─▶ tool calls (allow-listed, audited)
                    │                        │
                    │                        ├─ deterministic services (numbers, policy, rules)
                    └──── human interrupts ◀─┘
                         (await replies / verify / approval)
```

## 1. Orchestrator graph

`app/agents/graph.py`, thirteen stages:

`intake → clarify → normalize → supplier_select → rfq_generate → dispatch → await_replies →
parse → verify → negotiate → compare → recommend → approval`

* `HUMAN_INTERRUPTS = {await_replies, verify, approval}` — the graph stops there and reports
  `waiting_for_human: true`. It cannot run through an award.
* Every run creates an `agent_run` row; every stage creates an `agent_step` row with input, output,
  reasoning, status and latency.
* A stage exception is caught, recorded (`step.status = "error"`, `run.status = "failed"`) and the
  run stops with `blocked_reason` — the API never 500s because a stage misbehaved.
* `dispatch` is deferred unless a human passes `auto_dispatch`, and even then the policy engine
  checks hours, opt-in and daily caps per supplier.
* `stop_before` lets an operator or test run only a prefix of the graph.

## 2. Tool registry (the only way the agent can act)

`app/agents/tools.py`. Each entry is a `ToolSpec(name, description, input_model, permission,
handler, mutating, requires_policy)`:

| Tool | Permission | Mutating | Notes |
| --- | --- | --- | --- |
| `search_suppliers` | `supplier:discovery` | – | discovery job, buyer approval required before use |
| `fetch_public_page` | `supplier:discovery` | – | SSRF-guarded, robots.txt respected |
| `normalize_item` | `pr:create` | – | UNSPSC mapping with confidence |
| `create_rfq_draft` | `rfq:create` | ✓ | draft only, never dispatched |
| `send_rfq` | `rfq:send` | ✓ | requires policy |
| `parse_offer` | `offer:verify` | ✓ | produces a low-confidence extraction for review |
| `propose_counter` | `negotiation:draft` | ✓ | requires policy |
| `send_message` | `negotiation:send` | ✓ | requires policy |
| `request_clarification` | `pr:update` | ✓ | asks the buyer, not the supplier |
| `request_approval` | `approval:read` | ✓ | routes to a human chain |
| `compute_tco` | `offer:read` | – | deterministic maths |
| `get_price_benchmark` | `offer:read` | – | anonymised aggregates only |

**Forbidden by construction** — `accept_offer`, `accept_counter_offer`, `sign_contract`, `sign`,
`pay_invoice`, `execute_payment`, `record_payment`, `approve_invoice`, `create_invoice`,
`confirm_receipt`, `accept_delivery`, `close_po`, `change_specifications`, `change_spec`,
`edit_rfq_specs`, `approve_offer`, `issue_po`, `commit_purchase`. An `assert` at import time fails the process if any
of these is ever registered, and `invoke_tool` refuses them by name before touching the session.

Every call is validated (Pydantic), authorised (RBAC on the current user's roles), executed inside
a `tool_call` audit row (args, result, latency, status, error) and normalised through `json_safe()`
so a `Decimal` can never break the audit write.

## 3. Policy engine (deterministic)

`app/agents/policy_engine.py` — pure functions over `PolicySpec` (target, walk-away, max rounds,
allowed concessions, mode, value threshold, currency, tone) and `PolicyContext` (round, RFQ value,
supplier status, sanctions, opt-in, channel, working hours, tenant mode ceiling, approval limit).

Returns `Decision(allowed, action, codes, reasons, warnings, proposed_price, auto_value_ok)`, where
`action ∈ {send, keep_draft, draft_ok, block}`.

| Code | Rule |
| --- | --- |
| `BLOCK_ACCEPT_INTENT` / `BLOCK_SIGN_INTENT` / `BLOCK_SPEC_CHANGE` | forbidden intent |
| `BLOCK_PRICE_ABOVE_WALKWAY` | proposed price > walk-away (absolute) |
| `BLOCK_INVALID_PRICE` | price ≤ 0 |
| `BLOCK_ROUND_LIMIT` | round > policy max |
| `BLOCK_CONCESSION_NOT_ALLOWED` | concession outside the allow-list |
| `BLOCK_COMPETITOR_DISCLOSURE` | competitor name or exact price detected |
| `BLOCK_SUPPLIER_BLACKLISTED` / `BLOCK_SANCTIONS` | eligibility |
| `BLOCK_NO_OPT_IN` / `BLOCK_CHANNEL_UNAVAILABLE` | consent and channel |
| `BLOCK_OUTSIDE_HOURS` | autonomous send outside supplier contact hours |
| `BLOCK_AUTO_VALUE_THRESHOLD` / `BLOCK_AUTO_MODE_DISABLED` | autonomy ceilings |
| `WARN_ABOVE_TARGET` | price above target but under walk-away |
| `WARN_APPROVAL_REQUIRED` | value above sender's approval limit |
| `WARN_DRAFT_ONLY` | tenant mode forces a human send |

Verified by a 60-case × parameter matrix against an independent reference implementation
(`tests/test_negotiation_guardrails.py`, 122 parametrised cases) plus `tests/test_policy_engine.py`.

## 4. Guardrails against untrusted content

`app/agents/guardrails.py`:

1. **Fencing** — supplier text is wrapped in `<untrusted_supplier_content>` and passed as data in a
   user message; system prompts never interpolate supplier text (`app/agents/prompts/__init__.py`
   documents this and `PROMPT_VERSION` versions the contract).
2. **Normalisation** — zero-width and bidi-control characters are stripped; length is capped at
   20 000 chars.
3. **Scoring** — phrases such as "ignore previous instructions", "system:", "reveal your prompt",
   Arabic equivalents, and instruction-like patterns raise `ScanResult.flagged` with codes. Flagged
   replies are still parsed (data is data) but force `needs_review` and are audited as
   `PROMPT_INJECTION_SUSPECTED`.
4. **Output checks** — outbound text is scanned for competitor names and near-exact competitor
   prices (`competitor_references`), and recommendations are number-checked
   (`validate_numbers`) so a fabricated figure is replaced by a deterministic summary.
5. **Corpus** — `tests/test_injection_corpus.py` runs the 40-payload corpus (Arabic and English variants of every
   pattern) and asserts no payload can change commercial state.

## 5. LLM abstraction

`app/agents/llm/` — one protocol, three implementations:

| Provider | Selection | Notes |
| --- | --- | --- |
| `AnthropicProvider` | `LLM_PROVIDER=anthropic` (default for production) | Messages API, JSON-mode prompting, exact token accounting |
| `OpenAICompatibleProvider` | `LLM_PROVIDER=openai_compatible` + `LLM_BASE_URL` | any OpenAI-compatible gateway (Azure, vLLM, OpenRouter) |
| `FakeLLM` | `LLM_PROVIDER=fake` (default in dev/test) | deterministic, offline, same request/response contract |

`MeteredLLM` wraps every provider: it enforces a per-call token budget, computes cost from a price
table, writes `usage_event` rows per tenant, and records model/tokens/latency on the `agent_run`.

## 6. Human-in-the-loop points

| Point | Where | What the human does |
| --- | --- | --- |
| Clarification | `pr.clarification_resolved` | answers the questions the agent asked |
| Field verification | `POST /extractions/{id}/fields/{field_id}/verify` | confirms or corrects a low-confidence value |
| Extraction verification | `POST /extractions/{id}/verify` | unlocks comparison |
| Negotiation draft | `POST /negotiations/{id}/send` | revalidated against the current policy, then sent |
| Award approval | `POST /approvals/{id}/decide` | approve / reject / revision / split — the only way to a PO |
| Goods receipt | `POST /purchase-orders/{id}/receipts` (`receipt:create`) | the warehouse records what physically arrived |
| Delivery sign-off | `POST /receipts/{id}/confirm` (`receipt:confirm`) | the requesting team accepts quantity/quality/spec — the warehouse cannot sign off its own receipt |
| Invoice approval | `POST /invoices/{id}/approve` (`invoice:approve`) | finance approves a matched invoice; a mismatch needs a written `override_reason` that lands in the audit trail |
| Payment recording | `POST /invoices/{id}/payments` (`payment:record`) | finance books a transfer that already happened in the bank/ERP, with a mandatory reference |

### The post-award loop has no agent at all

Delivery and settlement are deliberately outside the orchestration graph (`STAGES` still ends at
`approval`). There is no stage, no tool and no LLM call that can receive goods, match an invoice,
approve it or move money:

* `receiving.py` and `invoicing.py` are pure services invoked by named humans through RBAC-guarded
  endpoints (`receipt:create`, `receipt:confirm`, `invoice:create`, `invoice:approve`,
  `payment:record`, `ledger:read`).
* The three-way match is **deterministic** (`compute_match`): quantities are compared with the
  confirmed receipt, prices with the PO line within a 0.5% tolerance. The model never sees the raw
  comparison and cannot mark anything as matched.
* The payment *suggestion* (`suggest_payment`) is rule-based over supplier country, currency, value
  and payment terms. Every result carries `requires_human_approval: true` and
  `execution: "outside_platform"`, and there is no payment-execution adapter in the codebase to call.
* `tests/test_agents_graph.py::test_post_award_human_actions_have_no_agent_tool` fails the build if a
  `record_payment`, `approve_invoice`, `create_invoice`, `confirm_receipt`, `accept_delivery` or
  `close_po` tool is ever registered.

## 7. Evaluation harness

* **Reply extraction** — `tests/test_extraction_harness.py` drives 150 synthetic supplier replies
  (Arabic/English, text/PDF/Excel/WhatsApp/voice) through the real parser and asserts ≥ 95% field
  accuracy with provenance for every extracted value.
* **Injection** — 40-payload corpus (86 parameterised cases), must not change state.
* **Negotiation guardrails** — 122 parametrised cases against an independent oracle.
* **Number faithfulness** — fabricated-figure fallback is a regression test
  (`tests/test_services_award_discovery.py`).

## 8. Known limits (honest)

1. The graph is implemented as an explicit stage runner rather than LangGraph; it gives the same
   guarantees (checkpointing, human interrupts, per-step audit) with far fewer moving parts.
2. Tool-call planning is a fixed workflow, not free-form model planning: the model writes text and
   proposes prices inside the workflow. This is a deliberate safety trade-off.
3. Real provider latency/cost numbers come from `MeteredLLM` at runtime; the test suite uses the
   fake so no real API is called in CI.
4. Voice calls use the telephony adapter's scripted flow; a full duplex STT/TTS loop is an extension
   point behind the same adapter interface.

## 9. Phase-4 re-verification of the safety principles

Phase 4 added concurrency control, not capability. The audits below are the ones that must never be
"fixed" by widening the agent's surface:

| Principle | What still holds after the hardening | Proof |
| --- | --- | --- |
| The LLM proposes, the human decides | The acceptance cycle only reaches a purchase order through `approval.decide` calls made by named users; the agent stages end at `approval` | `tests/test_api_phase4_full_cycle.py` (approval chain), `tests/test_agents_graph.py` |
| No autonomous acceptance, signing or payment | No tool, stage or LLM call touches `record_payment`, `approve_invoice`, `confirm_receipt`, `close_po`; the payment suggestion is `requires_human_approval: true` with `execution: outside_platform` | `test_post_award_human_actions_have_no_agent_tool`, `tests/test_services_invoicing.py`, `AC-P4-4` |
| Voice and messaging agents never approve or commit | `voice_agent.classify_question` returns a fixed refusal plus a `block_code` for competitor prices, commitments, award intent, spec changes and payment; the assistant's forbidden intents return `ASSISTANT_FORBIDDEN_ACTION` and `can_act: false` | `tests/test_services_voice_agent.py`, `tests/test_services_assistant.py` |
| Supplier content is untrusted | Inbound WhatsApp/email/voice text still goes through the same scanning and human verification before an offer exists — the phase-4 channel work did not add a path from an inbound message to an award | `tests/test_injection_corpus.py`, `tests/test_api_phase4_full_cycle.py` (extraction → verification) |
| The books cannot be moved by a race | The new locks protect *human* actions from concurrent duplicates; they grant the agent nothing | `scripts/concurrency_check.py`, `docs/DECISIONS.md` 89–91 |
