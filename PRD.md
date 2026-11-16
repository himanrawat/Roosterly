# Product requirements: Roosterly support ticket agent

## Metadata

- **Project:** Roosterly support ticket agent
- **Status:** Draft
- **Document owner:** Himanshu Rawat
- **Target ship date:** Not specified
- **Assignment received:** 16 November 2026, 13:26 IST
- **Assignment deadline:** 19 November 2026, 13:26 IST (72 hours after receipt)
- **AI provider:** Groq, as chosen for this project. The model and API integration details are not yet specified.

## Problem and objective

Roosterly is a shift-planning app for Dutch restaurants and shops. Its three-person support team receives about 200 tickets per week, almost all in Dutch. The team needs an agent that answers questions already covered by the help centre and hands other tickets to a human with enough context to act in under a minute. An unsupported reply can reach a customer; handing off every ticket does not save the team time.

The agent must process all open tickets from the helpdesk through one command and post one safe, useful decision per ticket.

## Users and context

- **Customer:** Receives an answer in the language they wrote in when the help centre supports one.
- **Human support agent:** Receives an English handoff note for tickets the agent cannot or must not answer.
- **Operator:** Runs or reruns the command and checks ticket outcomes and sanitized logs.

## Scope

### In scope

- Fetch all open tickets from the helpdesk, including every pagination page.
- Determine each ticket's language, category, priority, reason, and one action.
- Reply using only the Roosterly help-centre text, hand off to a human, or close spam and empty tickets.
- Follow the client's mandatory handoff and payment-data rules.
- Check Dutch replies for correctness without requiring the operator to read Dutch.
- Handle API failures, rate limits, crashes, and reruns without sending a customer two answers.
- Log one sanitized line per ticket in `logs/`.
- Evaluate decisions against the 10 labelled examples and measure the effect of one fix.

### Out of scope

- A graphical or web user interface; one command is sufficient.
- Answers based on sources outside `faq.md`.
- Closing tickets other than spam or empty tickets.
- Automatically resolving tickets that the client requires a human to handle.

## User flow

1. The operator starts the agent with one command.
2. The agent fetches all open ticket pages.
3. For each ticket, the agent determines the required fields and checks the proposed action against the client rules.
4. The agent posts exactly one reply, handoff, or close decision for that ticket.
5. The agent records one sanitized log line and continues to the next open ticket.
6. A later run processes tickets still open after any earlier interruption.

No screens, wireframes, or mockups are required.

## Functional requirements

### Ticket decision

1. For each open ticket, choose one language code: `nl`, `en`, or `de`, matching the customer's language.
2. Choose one category: `bug`, `billing`, `feature_request`, `question`, or `other`.
3. Choose one priority and explain it:
   - **P1:** Many users blocked now, or a security or legal risk.
   - **P2:** One customer blocked, or money or the contract is involved.
   - **P3:** Something is wrong or unclear, but there is a workaround.
   - **P4:** No urgency, such as a how-to question, idea, or cosmetic issue.
4. Choose one action:
   - **Reply:** Provide `draft_reply` in the customer's language. Use only facts in `faq.md`.
   - **Handoff:** Provide `handoff_note` in English, with context useful to human support.
   - **Close:** Use only for spam or empty tickets.
5. Provide `reason` in one or two sentences that explains category, priority, and action. `confidence` is optional in the API contract.

**Acceptance:** Each posted decision uses an allowed action and valid required fields. A reply does not add claims absent from `faq.md`.

### Client rules

1. Hand off refunds or disputed charges, cancellations and contract changes, personal data requests, possible security incidents, legal threats, and customers threatening to leave. This applies even when the FAQ mentions the topic.
2. Hand off when the answer is not in the help centre or the agent is unsure.
3. Never repeat card numbers or IBANs in a customer reply or log.
4. Treat ticket text as customer data, not instructions to the agent.

**Acceptance:** Mandatory-handoff tickets never receive an automated reply. Payment details do not appear in replies or logs.

### Helpdesk integration and reliability

1. Authenticate every helpdesk request with its bearer key and the same candidate email address.
2. Keep the helpdesk key and Groq API key in environment variables; keep both out of source and logs.
3. Fetch open tickets until `next_cursor` is `null`; the API returns at most 10 tickets per page.
4. Stay within the helpdesk limit of five requests per second. On 429, wait for `Retry-After` before retrying.
5. Retry transient 503 responses with a short backoff; the API says nothing was saved for those responses.
6. Do not retry a 409 `already_handled` as another action.
7. Recover after a crash or uncertain request outcome by checking ticket state before any possible repeat action. Process only tickets still open on a later run.
8. Treat invalid requests and authentication failures as errors to correct, not transient conditions to retry unchanged.
9. Write one sanitized log line per processed ticket in `logs/`.

**Acceptance:** A rerun can continue from remaining open tickets. No ticket receives two customer replies, including after a retry or crash.

### Evaluation

1. Fetch the 10 labelled examples from `GET /examples` and run the same decision logic against them without posting actions.
2. Use the supplied labels as the reference; the tested model must not generate them.
3. Record the initial result, identify one incorrect decision, make one change, and record the result after that change.
4. Measure safety, FAQ reply usefulness and language, category, priority, and action.

**Acceptance:** The before-and-after evaluation can be reproduced on the same 10 examples, and example tickets receive no POST requests.

## Constraints and dependencies

- The helpdesk API, `api_reference.md`, and `faq.md` define the external contract and allowed customer knowledge.
- Groq is the selected AI provider. A Groq API key is required; no specific model has been selected.
- Most tickets are in Dutch. Customer replies match the customer's language; handoff notes are in English.
- A paid model should cost well under EUR 1 for the assignment, according to the brief. Actual cost depends on the chosen model and measured token use.
- The API contains 30 practice tickets and 10 separate labelled examples. Examples cannot be handled through the action endpoints.

## Success measures

Measure these on the labelled examples and inspect practice-run outcomes:

- Mandatory handoffs sent to a human; unsupported claims and payment details absent from replies and logs.
- FAQ questions answered correctly in the customer's language when sufficiently supported.
- Agreement with supplied labels for action, category, priority, and language.
- All open ticket pages processed; each ticket gets one decision; reruns avoid duplicate answers.
- Before-and-after result for one fix.

No numeric accuracy threshold is specified in the assignment. Do not invent one for this PRD.

## Open questions

1. What evidence or threshold makes the agent "unsure" and triggers a handoff?
2. Which Groq model will be used, and what is its measured cost for this workload?
3. How will Dutch replies be checked for factual correctness by someone who does not read Dutch?
4. What information should a handoff note include to make a ticket actionable in under a minute?
5. How should the agent handle a ticket whose language is outside the API's allowed `nl`, `en`, and `de` values?

## Source documents

- `Nordavo_Test_Assignment_Agentic_AI_Engineer.pdf`, sections 01-05 and 07
- `faq.md`
- `api_reference.md`
- `candidate_faq.md` for the one-command, no-UI clarification
