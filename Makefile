SHELL := /bin/sh

.PHONY: test test-unit test-integration format lint

test: test-unit

test-unit:
	pytest -q -m "not integration"

test-integration:
	pytest -q -m integration

format:
	black .

lint:
	mypy app
