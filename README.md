# Roosterly support ticket agent

## How to run

Python 3.10+, macOS or Linux.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then put your real keys and email in .env
python -m agent         # all open tickets; safe to run again
```

Also: `python -m agent --dry-run`, `python -m unittest discover -s tests`, `python -m agent.evaluate --label mine --runs 3`.

## Kaggle GPU alternative

Groq remains the default. Set `AGENT_PROVIDER=local` to run `Qwen/Qwen3-8B` on a CUDA GPU instead; the same parsing, safety rules, helpdesk client and run loop apply. The local provider uses 4-bit loading and requires `requirements-kaggle.txt`. `GROQ_API_KEY` is optional for this provider. Switch back with `AGENT_PROVIDER=groq`.

For Kaggle, use [the evaluation notebook](kaggle/roosterly_gpu_eval.ipynb) in a **private** notebook with `GPU T4 x2` and Internet enabled. Add `HELPDESK_API_KEY` and `CANDIDATE_EMAIL` as Kaggle Secrets. The notebook clones this repository, installs the GPU dependencies, and runs the ten labeled examples without posting actions. The new provider code must be pushed to the GitHub repository before that clone step can work. Review the model's accuracy and full replies before running `python -m agent` to post tickets. Keep secrets and ticket text out of notebook output.

Kaggle is a batch runtime with limited GPU hours, so start a notebook only for an evaluation or ticket run. Model load and generation have not yet been verified on Kaggle hardware.

## Decisions

1. **The model proposes, code decides.** Groq (`openai/gpt-oss-120b`, strict JSON) or the local Kaggle model proposes a decision; `agent/rules.py` has the final word. Tickets it detects as refund, cancellation, personal data, security, legal or churn (patterns in nl/en/de) are handed off whatever the model said. Close is allowed only for spam or empty tickets. Recognised card numbers and IBANs are removed from the ticket text before the model call and from everything posted.
2. **"Unsure" is a rule in code.** A reply needs confidence of 0.75 or more and a cited FAQ section. Its numbers, links, menu labels and plan prices are checked against that section (the customer's own numbers and a correct price total are allowed), and a clear language mismatch with the ticket is caught. Otherwise: handoff.
3. **The helpdesk decides what is already answered,** not a local file, so a rerun is safe on any machine.

**Dutch.** The agent works in Dutch directly; I do not read it. A separate model call, which sees neither FAQ nor ticket, translates each Dutch or German reply to English. Each sentence must share half its content words with the cited FAQ section (60% if it names a plan) and may not use a promise, money or deadline word the FAQ lacks. Cost: one extra call per Dutch or German reply.

**AI tools.** Built with Claude Code and Codex CLI: Codex wrote the safety layer, Claude Code the engine, run loop and evaluation, and each reviewed the other's work. I ran the tests and practice runs myself.

**Assumptions.** A language outside nl/en/de is posted as `en` and handed off. An upgrade is not a contract change; a downgrade is. One model failure is handed off; three in a row stop the run.

## Reliability

- **503:** up to six retries with backoff. **429:** waits `Retry-After`; the client stays at 4 requests per second. **409:** logged, never retried.
- **Crash:** a pending file is written before each POST and removed after the log line. The next run asks the helpdesk about each pending ticket and never re-posts from the file. `scripts/crash_test.py` killed the agent three times mid-run: 12 tickets, 12 log lines, no 409. In the final run one POST timed out; the agent saw the ticket was handled and did not post again.
- **Security:** keys come from environment variables; ticket text is passed as JSON data and the code rules apply whatever it says; `logs/` holds ids and rule names, no free text.

## Eval and fix

`agent/evaluate.py` runs the real decision path on the 10 labelled examples, read-only. Averages of 3 runs, out of 10:

| | Category | Action | Priority | Language | Unsafe |
|---|---|---|---|---|---|
| Before | 7.33 | 7.33 | 7.67 | 10 | 0 |
| After | 9.67 | 7.67 | 8.0 | 10 | 0 |

**Wrong:** price and invoice how-to questions were filed as `question`; the labels say `billing`. **Fix:** one prompt paragraph (classify by topic). The safety layer was tuned on these examples before the baseline, so the numbers are optimistic.

**Final run:** 30 tickets: 10 replies, 18 handoffs, 2 closes. **Cost per 100 tickets: about $0.06:** (10,710 input × $0.15 + 59,392 cached × $0.075 + 20,151 output × $0.60) / 1M = $0.0182 for 30, × 100/30 = $0.061. `python scripts/preview.py T005` shows a full reply or handoff note, decided again without posting, so wording can differ from what was posted.

## What I would improve

**Next step:** besides exact checks, the Dutch check rests on word overlap. It blocks some correct replies (examples E01 and E05 are handed off) and can pass a false sentence built from FAQ words. I would have a second model judge each sentence against the FAQ. Left out: no check that a handoff note is in English; no Dutch reader has reviewed the replies.

**One change to the brief:** let the API accept a language outside `nl`, `en`, `de`.
