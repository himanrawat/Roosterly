# Roosterly support ticket agent

## How to run

Python 3.10+ on macOS or Linux.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # HELPDESK_API_KEY, CANDIDATE_EMAIL, GROQ_API_KEY
python -m agent             # all open tickets; safe to run again
```

Also: `python -m agent --dry-run` (posts nothing), `python -m unittest discover -s tests`, `python -m agent.evaluate --label mine --runs 3`.

## Decisions

1. **The model proposes, code decides.** `agent/decide.py` asks Groq (`openai/gpt-oss-120b`, strict JSON) for a decision. `agent/rules.py` has the final word: tickets it detects as refund, cancellation, personal data, security, legal or churn (patterns in nl/en/de) are handed off whatever the model said; close is allowed only for spam or empty tickets; recognised card numbers and IBANs are removed before the text reaches the model and from everything posted.
2. **"Unsure" is a rule in code.** A reply needs confidence of 0.75 or more and a cited FAQ section. Its numbers, links and menu labels are checked against that section; the customer's own numbers and a verified price total (25 × €6 = €150) are allowed. Otherwise: handoff.
3. **The helpdesk decides what is already answered,** not a local file, so a rerun is safe on any machine.

**Dutch.** The agent works in Dutch directly; I do not read it. A separate model call, which sees neither FAQ nor ticket, translates each Dutch or German reply to English. Each sentence, greetings aside, must share half its content words with the cited FAQ section (60% if it names a plan) and may not use a promise, money or deadline word the FAQ lacks. Cost: one extra model call per such reply.

**AI tools.** Built with Claude Code and Codex CLI. Codex wrote the safety layer; Claude Code wrote the engine, run loop and evaluation; each reviewed the other's work before it was merged. I ran the tests and the practice runs myself.

**Assumptions.** A language outside nl/en/de is posted as `en` and handed off. An upgrade is not a contract change; a downgrade is. One model failure is handed off; three in a row stop the run.

## Reliability

- **503:** up to six retries with backoff. **429:** waits `Retry-After`; the client stays at 4 requests per second. **409:** logged, never retried.
- **Crash:** a pending file is written before each POST and removed after the log line. The next run asks the helpdesk about each pending ticket and never re-posts from the file. A timed-out POST is checked the same way. `scripts/crash_test.py` killed the agent three times mid-run: 12 tickets, 12 log lines, no 409.
- **Security:** keys are read from environment variables (`.env` is git-ignored); ticket text is passed as JSON data, and the code rules apply whatever the ticket says; `logs/` holds ids, enum values and rule names, no free text.

## Eval and fix

`agent/evaluate.py` runs the real decision path on the 10 labelled examples through a client that refuses every non-GET request. Averages of 3 runs, out of 10 (`eval/before.json`, `eval/after.json`):

| | Category | Action | Priority | Language | Unsafe |
|---|---|---|---|---|---|
| Before | 7.33 | 7.0 | 8.0 | 10 | 0 |
| After | 10 | 7.33 | 8.0 | 10 | 0 |

**Wrong decision:** price and invoice how-to questions were filed as `question`; the labels say `billing`. **Fix:** one prompt paragraph (classify by topic, not question form). Category was the target; action also varied slightly between runs. The safety layer was tuned on these same examples before the baseline, so the numbers are optimistic.

**Final run** (`eval/final_run.json`): 30 tickets, 10 replies, 18 handoffs, 2 closes. **Cost per 100 tickets: about $0.06.** 69,891 input tokens (61,440 cached) and 19,168 output: (8,451 × $0.15 + 61,440 × $0.075 + 19,168 × $0.60) / 1M = $0.0174 for 30, × 100/30 = $0.058.

## What I would improve

**Next step:** the back-translation check is word overlap. It can pass a false sentence built from FAQ words, and it blocks correct replies: examples E01 and E05 are handed off although the FAQ answers them. I would have a second model judge each sentence against the FAQ and save the translation for a human to read. Left out: no check that a handoff note is English; no Dutch reader has reviewed the replies.

**One change to the brief:** let the API accept a language outside `nl`, `en`, `de`; today a French ticket must be filed under a wrong code.
