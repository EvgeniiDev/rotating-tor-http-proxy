PYTHON ?= python3
PACKAGE = rotating_tor_proxy

.PHONY: install lint test format run

install:
	pip install -e .

install-dev:
	pip install -e .[dev]

lint:
	ruff check src tests

format:
	ruff format src tests

test:
	pytest --maxfail=1 --disable-warnings -q

run:
	python -m $(PACKAGE).main