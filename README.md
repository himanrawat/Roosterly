# Roosterly support ticket agent

## How to run

Python 3.10+, macOS or Linux. From a fresh clone:

```bash
git clone https://github.com/himanrawat/Roosterly.git && cd Roosterly
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add the helpdesk key, your application email and Groq key
python -m agent
```

Check without posting: `python -m agent --status`, `python -m unittest discover -s tests`, or `python scripts/preview.py T005`.

## Decisions

1. **Model proposes; code decides.** Groq `openai/gpt-oss-120b` returns strict JSON. Code forces mandatory handoffs, closes only spam or empty tickets, and scrubs card numbers and IBANs.
2. **Uncertain replies go to a human.** Replies need confidence ≥0.75 and a cited FAQ section; code checks numbers, links, menu labels and prices. The agent works in Dutch directly. One extra call back-translates Dutch or German replies for sentence-level FAQ checks.
3. **The helpdesk is the source of truth.** Ticket state is rechecked before posting. I assume unsupported languages use `en` with a handoff; upgrades are not contract changes, downgrades are; three consecutive model failures stop the run.

I used Claude Code and Codex CLI to build and review separate parts, then ran the tests, eval and practice runs myself.

## Reliability

At most four requests/second; 503 gets six retries, 429 waits `Retry-After`, 409 is never retried. After a POST timeout or crash, a pending file and a ticket-state check prevent duplicate answers; three crash tests found none. Keys come from environment variables; ticket text is untrusted data; logs contain IDs and rule names, never ticket text or payment details.

## Eval and fix

`agent/evaluate.py` uses 10 API labels without posting or exposing labels to the model. Three-run averages (out of 10):

| | Category | Action | Priority | Language | Unsafe proxy |
|---|---:|---:|---:|---:|---:|
| Before | 7.33 | 7.33 | 7.67 | 10 | 0 |
| After | 9.67 | 7.67 | 8.00 | 10 | 0 |

One prompt change classified price and invoice questions as `billing`. Safety rules were tuned before this baseline, so results are optimistic. Final run: 30 handled (7 replies, 21 handoffs, 2 closes).

**Cost per 100 tickets ≈ $0.064:** (35,101 input × $0.15 + 34,816 cached input × $0.075 + 19,032 output × $0.60) / 1,000,000 × 100/30. Rates are USD per million tokens.

## What I would improve

Next: add an entailment check and Dutch-speaker review; the current check can reject valid replies or miss false claims. I left out automatic English checks for handoff notes. I would let the API accept languages beyond `nl`, `en` and `de`.
