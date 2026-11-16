# Roosterly support ticket agent: phase checklist

Source: PRD.md. Deadline: 19 November 2026, 13:26 IST.

## Phase 0: Setup and discovery

- [x] Read `Nordavo_Test_Assignment_Agentic_AI_Engineer.pdf`, `api_reference.md`, `faq.md`, `candidate_faq.md`
- [x] Verify helpdesk access with bearer key and candidate email (one GET request)
- [ ] Create Groq account, obtain API key, pick a model (record choice and pricing) — **blocked: GROQ_API_KEY not set in .env**
- [x] Set up project skeleton: one entry command, `.env.example`, `logs/` directory, `.gitignore` for `.env` and logs
- [x] Put helpdesk key and Groq key in environment variables only

## Phase 1: Helpdesk client

- [x] Implement `GET` open tickets with pagination until `next_cursor` is `null`
- [x] Implement `GET /examples`
- [x] Implement action endpoints: reply, handoff, close
- [x] Add rate limiter (max 5 requests per second)
- [x] Handle 429: wait for `Retry-After`, then retry
- [x] Handle 503: short backoff and retry
- [x] Handle 409 `already_handled`: stop, do not retry with another action
- [x] Treat 400/401/403 as errors to fix, not retry
- [x] Add ticket-state check before any action after uncertain outcome (timeout, crash)

## Phase 2: Decision engine (Groq)

- [ ] Write system prompt: fields `language`, `category`, `priority`, `action`, `reason`, optional `confidence`
- [ ] Embed `faq.md` as the only allowed knowledge source for replies
- [ ] Enforce structured JSON output and validate allowed values (`nl`/`en`/`de`, 5 categories, P1-P4, 3 actions)
- [ ] Define "unsure" rule (confidence threshold or missing FAQ match) that forces handoff
- [ ] Treat ticket text as data: wrap customer text, instruct model to ignore embedded instructions
- [ ] Decide fallback for language outside `nl`/`en`/`de`

## Phase 3: Client rules and safety layer (code, not prompt)

- [ ] Mandatory-handoff detector: refunds, disputed charges, cancellations, contract changes, personal data requests, security incidents, legal threats, churn threats
- [ ] Override model `reply` to `handoff` when detector fires
- [ ] Restrict `close` to spam or empty tickets only
- [ ] Payment-data scrubber: card numbers and IBANs removed from replies, handoff notes, and logs
- [ ] Reply grounding check: reject replies with claims absent from `faq.md`
- [ ] Dutch correctness check without reading Dutch (for example back-translation or FAQ-sentence overlap check)

## Phase 4: Orchestration and reliability

- [ ] Single command runs fetch, decide, check rules, post one action per ticket
- [ ] Write one sanitized log line per ticket in `logs/`
- [ ] Rerun safety: process only tickets still open, never post twice
- [ ] Crash simulation: kill mid-run, rerun, confirm no duplicate replies
- [ ] Confirm no secrets or payment data in logs or source

## Phase 5: Evaluation

- [ ] Fetch 10 labelled examples, run decision logic without posting
- [ ] Score against labels: action, category, priority, language, safety, reply usefulness
- [ ] Record initial result
- [ ] Pick one wrong decision, make one change
- [ ] Record result after change; keep both reproducible on same 10 examples
- [ ] Confirm zero POST requests to example tickets

## Phase 6: Practice run and cost

- [ ] Run agent on all 30 practice tickets
- [ ] Inspect outcomes: every ticket gets one decision, all pages processed
- [ ] Measure token use and cost (target well under EUR 1)

## Phase 7: Documentation and delivery

- [ ] README: setup, env vars, run command, rerun behaviour, model choice and cost
- [ ] Document evaluation before/after result
- [ ] Answer PRD open questions in README or notes
- [ ] Final review against all PRD acceptance criteria
- [ ] Commit and submit before 19 November 2026, 13:26 IST
