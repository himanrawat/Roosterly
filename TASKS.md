# Roosterly support ticket agent: phase checklist

Source: PRD.md. Deadline: 19 November 2026, 13:26 IST.

## Phase 0: Setup and discovery

- [x] Read `Nordavo_Test_Assignment_Agentic_AI_Engineer.pdf`, `api_reference.md`, `faq.md`, `candidate_faq.md`
- [x] Verify helpdesk access with bearer key and candidate email (one GET request)
- [x] Create Groq account, obtain API key, pick a model (record choice and pricing) — `openai/gpt-oss-120b`; $0.15 input, $0.075 cached input, $0.60 output per 1M tokens (console.groq.com/docs, read 17 Nov 2026)
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

- [x] Write system prompt: fields `language`, `category`, `priority`, `action`, `reason`, optional `confidence`
- [x] Embed `faq.md` as the only allowed knowledge source for replies
- [x] Enforce structured JSON output and validate allowed values (`nl`/`en`/`de`, 5 categories, P1-P4, 3 actions)
- [x] Define "unsure" rule (confidence threshold or missing FAQ match) that forces handoff
- [x] Treat ticket text as data: wrap customer text, instruct model to ignore embedded instructions
- [x] Decide fallback for language outside `nl`/`en`/`de`

## Phase 3: Client rules and safety layer (code, not prompt)

- [x] Mandatory-handoff detector: refunds, disputed charges, cancellations, contract changes, personal data requests, security incidents, legal threats, churn threats
- [x] Override model `reply` to `handoff` when detector fires
- [x] Restrict `close` to spam or empty tickets only
- [x] Payment-data scrubber: card numbers and IBANs removed from replies, handoff notes, and logs
- [x] Reply grounding check: reject replies with claims absent from `faq.md`
- [x] Dutch correctness check without reading Dutch (for example back-translation or FAQ-sentence overlap check)

## Phase 4: Orchestration and reliability

- [x] Single command runs fetch, decide, check rules, post one action per ticket
- [x] Write one sanitized log line per ticket in `logs/`
- [x] Rerun safety: process only tickets still open, never post twice
- [x] Crash simulation: kill mid-run, rerun, confirm no duplicate replies — `python scripts/crash_test.py` (posts to practice tickets)
- [x] Confirm no secrets or payment data in logs or source

## Phase 5: Evaluation

- [x] Fetch 10 labelled examples, run decision logic without posting
- [x] Score against labels: action, category, priority, language, safety, reply usefulness
- [x] Record initial result
- [x] Pick one wrong decision, make one change
- [x] Record result after change; keep both reproducible on same 10 examples — `eval/before.json`, `eval/after.json`; rerun with `python -m agent.evaluate`, compare with `--compare before after`
- [x] Confirm zero POST requests to example tickets

## Phase 6: Practice run and cost

- [x] Run agent on all 30 practice tickets
- [x] Inspect outcomes: every ticket gets one decision, all pages processed
- [x] Measure token use and cost (target well under EUR 1)

## Phase 7: Documentation and delivery

- [x] README: setup, env vars, run command, rerun behaviour, model choice and cost
- [x] Document evaluation before/after result
- [x] Answer PRD open questions in README or notes
- [x] Final review against all PRD acceptance criteria
- [x] Commit (local, on main)
- [ ] Push to GitHub, give `nordavo-hiring` read access if the repo is private, and reply to Nordavo's email with the repo link before 19 November 2026, 13:26 IST
