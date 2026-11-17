# Short commands for the agent. Run `make` to see them.
PY := .venv/bin/python

.PHONY: help run dry test eval compare status reset final crash

help:
	@echo "make run      process all open tickets (posts to the helpdesk)"
	@echo "make dry      decide all open tickets, post nothing (logs/dry_run.jsonl)"
	@echo "make test     offline tests"
	@echo "make eval     score the 10 labelled examples (read-only)"
	@echo "make compare  show the saved before/after evaluation"
	@echo "make status   open and handled ticket counts"
	@echo "make reset    re-open all 30 practice tickets"
	@echo "make final    reset, then one clean run of all 30 (the scored run)"
	@echo "make crash    live crash test (posts to practice tickets)"

run:
	$(PY) -m agent

dry:
	$(PY) -m agent --dry-run

test:
	$(PY) -m unittest discover -s tests

eval:
	$(PY) -m agent.evaluate --label latest --force

compare:
	$(PY) -m agent.evaluate --compare before after

status:
	$(PY) -m agent --status

reset:
	$(PY) -m agent --reset

final: reset run

crash:
	$(PY) scripts/crash_test.py
