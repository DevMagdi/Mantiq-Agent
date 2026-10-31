# End-to-end testing

Two layers, because they answer different questions.

| Layer | Question it answers | Runs where |
| --- | --- | --- |
| `scripts/e2e_journeys.py` | "Do the real integrations work together, across two replicas?" | Every CI build, and any laptop |
| `frontend/e2e/*.spec.ts` (Playwright) | "Does a browser actually render and drive this?" | A machine with browser binaries |

## 1. Headless journeys (always available)

```bash
python scripts/e2e_journeys.py            # from the repository root
```

The script builds its own environment and tears it down again:

1. migrates a temporary database (`alembic upgrade head`), bootstraps and seeds the demo tenant;
2. starts a real Redis (`redis-server`, from the `redislite` dev dependency or `PATH`);
3. starts **two** uvicorn replicas on free ports, both pointed at that Redis and the same database;
4. starts a vendor-side HTTP server that speaks the Twilio Messages API (including the account
   resource Twilio's own SDKs verify against), the Anthropic Messages API and the OpenAI
   chat-completions API;
5. leaves the *platform* on the fake providers (`WHATSAPP_PROVIDER/SMS_PROVIDER/TELEPHONY_PROVIDER/
   LLM_PROVIDER=fake`) and no vendor secret in the environment, so every real call below can only
   come from credentials saved through the Integrations screen;
6. opens one WebSocket per replica and drives the journeys.

It then asserts fifteen things, printing `PASS` per journey:

* sign in, token refresh (single-use cookie), sign out revokes the refresh session;
* requisition → clarification → submit → RFQ → dispatch;
* an outbound WhatsApp quote request that Twilio (the stub) really receives, with the `whatsapp:`
  addressing and the sender number;
* an **unsigned** inbound webhook is refused with `401`, a **signed** one is accepted;
* the resulting `offer.extracted` frame arrives on the socket of **both** replicas;
* a supplier reply is extracted by the real LLM provider over HTTP (`anthropic:<model>` recorded);
* `/channels/capabilities` reports the real provider;
* turning the WhatsApp feature flag off makes the server refuse a send (`BLOCK_FEATURE_OFF`);
* **integrations**: a tenant administrator saves a real Anthropic key and Twilio credentials on the
  Integrations screen — both adapters flip `sandbox`/`fake` → `live`, the secret is never echoed back
  (only `••••` + the last four characters), both probes really hit the vendor (the Twilio probe
  authenticates against `/2010-04-01/Accounts/{sid}.json`), and a reload still hides the secret;
* **integration safety**: `PATCH enabled:false` makes the adapter `disabled` and the next supplier
  reply is extracted with **no** vendor call and no `anthropic:` model recorded, then re-enabling
  restores the real provider.

Options: `--database-url postgresql+asyncpg://…` to use PostgreSQL, `--redis-url redis://…` to use an
existing Redis, `--keep` to keep the temporary database for inspection.

## 2. Browser suite (Playwright)

```bash
cd frontend
npx playwright install chromium     # needs network access to the Playwright CDN
npm run e2e                         # starts the Vite dev server itself (webServer in the config)
```

Prerequisites: the backend running on `http://127.0.0.1:8000` with the demo tenant seeded
(`procureai bootstrap --demo`), because the suite signs in with the demo accounts. To point it at a
deployed environment instead:

```bash
E2E_BASE_URL=https://staging.example.com npm run e2e
```

What the specs cover:

| Spec | Coverage |
| --- | --- |
| `auth.spec.ts` | sign-in form, wrong password, anonymous redirect, sign-out, Arabic (RTL) default and the English switch |
| `screens.spec.ts` | all 16 navigable routes mount without a console error or an error boundary, per role |
| `realtime.spec.ts` | the session token opens an authenticated socket, `connected` then `pong`, and an invalid token is closed with `4401` |
| `integrations.spec.ts` | the Integrations screen renders real per-provider fields (no JSON textarea), an incomplete save reports `Error` with the missing field names, and enable/disable round-trips through the API |

`npm run e2e:typecheck` compiles the suite under the project's strict TypeScript settings and runs in
CI, so the specs cannot rot silently even where browsers are unavailable.

### Why it is not in CI

The suite needs a browser binary (~150 MB) from `cdn.playwright.dev`. That host is blocked in this
environment (`ECONNRESET` during `npx playwright install`), so the suite has been written and
typechecked but **not executed** here. Adding a CI job that downloads browsers and boots both tiers is
a five-line change once a runner with network access is available; it is not enabled today because an
unverified job is worse than a documented gap.

## 3. Unit-level end-to-end

`tests/test_api_p2p_accounting_e2e.py` and `tests/test_api_phase4_full_cycle.py` still walk the whole
purchase-to-pay cycle over HTTP with eight personas. They are the fastest regression net; the two
layers above add "the providers are real" and "a browser works".
