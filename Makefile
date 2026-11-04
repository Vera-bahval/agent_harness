# Targets are enabled only when their implementation is ready.
.DEFAULT_GOAL := help
.PHONY: help plan up down test-ci test-ui test-infra test-contracts test-unit baf-smoke baf-cp1 baf bench-fs bench-sandbox
help:
	@echo "Каркас проекта: runtime пока не реализован."
	@echo "make plan — прочитать последовательность задач до ЧП1"
	@echo "Будущие команды: up down test-ci baf-smoke baf-cp1 baf"
plan:
	@cat docs/checkpoint-1.md
up down test-ci test-ui test-infra test-contracts test-unit baf-smoke baf-cp1 baf bench-fs bench-sandbox:
	@echo "NOT IMPLEMENTED: $@. См. docs/checkpoint-1.md" >&2
	@exit 2
