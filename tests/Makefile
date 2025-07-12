# Makefile for running tests in rotating-tor-http-proxy project
# Usage: make [target]

# Variables
PYTHON = python3
PYTEST = pytest
SRC_DIR = ../src
TEST_DIR = .
PYTHONPATH = PYTHONPATH=$(SRC_DIR)
TIMEOUT = timeout 30s

# Default target
.PHONY: help
help:
	@echo "Available targets:"
	@echo "  test            - Run all tests"
	@echo "  test-parallel   - Run parallel exit node tests"
	@echo "  test-basic      - Run basic integration tests"
	@echo "  test-full       - Run full integration tests"
	@echo "  test-simple     - Run simple tests"
	@echo "  test-debug      - Run debug tests"
	@echo "  test-verbose    - Run all tests with verbose output"
	@echo "  test-fast       - Run tests with short timeout"
	@echo "  clean           - Clean test artifacts"
	@echo "  lint            - Check code syntax"
	@echo "  demo            - Run demo approaches"
	@echo "  help            - Show this help message"

# Run all tests
.PHONY: test
test:
	@echo "Running all tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR) -v

# Run specific test files
.PHONY: test-parallel
test-parallel:
	@echo "Running parallel exit node tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) test_exit_node_parallel.py

.PHONY: test-basic
test-basic:
	@echo "Running basic integration tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest test_basic_integration.py -v

.PHONY: test-full
test-full:
	@echo "Running full integration tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest test_full_integration.py -v

.PHONY: test-simple
test-simple:
	@echo "Running simple integration tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest test_simple_integration.py -v

.PHONY: test-debug
test-debug:
	@echo "Running debug tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) debug_test.py

# Run tests with verbose output
.PHONY: test-verbose
test-verbose:
	@echo "Running all tests with verbose output..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR) -v -s

# Run tests with short timeout for quick checks
.PHONY: test-fast
test-fast:
	@echo "Running fast tests (10s timeout)..."
	timeout 10s env $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR) -x

# Clean test artifacts
.PHONY: clean
clean:
	@echo "Cleaning test artifacts..."
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.log" -delete 2>/dev/null || true
	@echo "Clean completed"

# Install test dependencies
.PHONY: install-deps
install-deps:
	@echo "Installing test dependencies..."
	pip install pytest pytest-timeout

.DEFAULT_GOAL := help
