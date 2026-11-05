.DEFAULT_GOAL := help
PYTHON ?= .venv/bin/python
PAIR ?= replay-b1
.PHONY: help plan up down status logs test-server test-sandbox test-lifecycle demo-sandbox test-harness demo-agent test-ci test-ui test-infra test-contracts test-unit baf-smoke baf-cp1 baf bench-fs bench-sandbox
help:
	@echo "ЧП1: test-ci — проверки; baf-cp1-replay — 15 offline; baf-cp1 — Sol low."
	@echo "make up / down / status / logs — управление сервером"
	@echo "make test-unit / test-contracts / test-server / test-sandbox / test-harness — проверки"
	@echo "make plan — последовательность задач до ЧП1"
plan:
	@cat docs/checkpoint-1.md
up:
	docker compose up --build -d --wait --wait-timeout 90
down:
	docker compose down -v --remove-orphans
status:
	docker compose ps
logs:
	docker compose logs --tail=100
test-contracts:
	$(PYTHON) -m unittest discover -s tests/contracts -p 'test_*.py' -v
test-unit:
	$(PYTHON) -m unittest discover -s tests/unit -p 'test_*.py' -v
test-server:
	$(PYTHON) scripts/check_server.py

test-sandbox:
	docker compose exec -T sandbox-manager python - < tests/infra/sandbox_scenarios.py
test-lifecycle:
	$(PYTHON) scripts/check_sandbox_lifecycle.py
demo-sandbox:
	docker compose exec -T harness python - < scripts/demo_sandbox.py

test-harness:
	docker compose exec -T harness python - < tests/infra/harness_scenarios.py
demo-agent:
	docker compose exec -T harness python - "$(PAIR)" < scripts/demo_agent.py

bench-fs bench-sandbox:
	@echo "NOT IMPLEMENTED: $@. См. docs/checkpoint-1.md" >&2
	@exit 2

.PHONY: setup-client demo-client test-client test-ui
setup-client:
	$(PYTHON) scripts/setup_client.py
demo-client:
	$(PYTHON) -m client.cli run 'На каком порту слушает сервис? Запиши число в answer.txt.' --pair $(PAIR)
test-ui:
	$(PYTHON) -m unittest discover -s tests/ui -p 'test_*.py' -v
test-client:
	$(PYTHON) tests/infra/client_scenarios.py
.PHONY: test-gateway-lifecycle
test-gateway-lifecycle:
	$(PYTHON) scripts/check_gateway_lifecycle.py

TASKS ?= B1.1
REPEATS ?= 1
.PHONY: baf-smoke baf-b1
baf-smoke:
	$(PYTHON) -m baf.runner --pair $(PAIR) --tasks $(TASKS) --repeats $(REPEATS) --mode smoke
baf-b1:
	$(PYTHON) -m baf.runner --pair first-real-pair --tasks B1.1 --repeats 1 --mode quality

.PHONY: baf-simple-smoke baf-simple-real
baf-simple-smoke:
	$(PYTHON) -m baf.runner --pair replay-simple --tasks B1.1 B2.1 B3.1 B4.1 B5.1 --repeats 1 --mode smoke
baf-simple-real:
	$(PYTHON) -m baf.runner --pair first-real-pair --tasks B1.1 B2.1 B3.1 B4.1 B5.1 --repeats 1 --mode quality

.PHONY: test-ci test-infra baf-cp1 baf-cp1-replay baf
test-infra: test-server test-sandbox test-harness test-client
test-ci: test-unit test-contracts test-ui test-infra baf-cp1-replay
baf-cp1 baf: PAIR = cp1-sol-low
baf-cp1:
	$(PYTHON) -m baf.runner --pair $(PAIR) --tasks all --repeats 1 --mode quality
baf-cp1-replay:
	$(PYTHON) -m baf.runner --pair replay-cp1 --tasks all --repeats 1 --mode smoke-all
baf:
	$(PYTHON) -m baf.runner --pair $(PAIR) --tasks all --repeats 3 --mode quality
.PHONY: test-clean
test-clean:
	$(PYTHON) scripts/check_clean_start.py
