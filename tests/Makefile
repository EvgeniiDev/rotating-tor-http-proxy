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
	@echo "  test-api        - Run API compatibility tests"
	@echo "  test-integrity  - Run module integrity tests"
	@echo "  test-contract   - Run API contract validation tests"
	@echo "  test-parallel   - Run parallel exit node tests"
	@echo "  test-basic      - Run basic integration tests"
	@echo "  test-basic-proxy - Run basic integration with proxy tests"
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

# API and integrity tests
.PHONY: test-api
test-api:
	@echo "Running API compatibility tests..."
	cd $(SRC_DIR) && $(TIMEOUT) $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR)/test_api_compatibility.py -v

.PHONY: test-integrity
test-integrity:
	@echo "Running module integrity tests..."
	cd $(SRC_DIR) && $(TIMEOUT) $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR)/test_module_integrity.py -v

.PHONY: test-contract
test-contract:
	@echo "Running API contract validation tests..."
	cd $(SRC_DIR) && $(TIMEOUT) $(PYTHONPATH) $(PYTHON) -m pytest $(TEST_DIR)/test_api_contract_validation.py -v

.PHONY: test-quality
test-quality: test-api test-integrity test-contract
	@echo "All quality tests completed"

# Run specific test files
.PHONY: test-parallel
test-parallel:
	@echo "Running parallel exit node tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) test_exit_node_parallel.py

.PHONY: test-basic
test-basic:
	@echo "Running basic integration tests..."
	$(TIMEOUT) env $(PYTHONPATH) $(PYTHON) -m pytest test_basic_integration.py::TestBasicIntegration::test_application_starts_and_shows_expected_output -v

.PHONY: test-basic-proxy
test-basic-proxy:
	@echo "Running basic integration tests with proxy validation..."
	timeout 120s env $(PYTHONPATH) $(PYTHON) -m pytest test_basic_integration.py -v

.PHONY: test-basic-standalone
test-basic-standalone:
	@echo "Running comprehensive standalone integration test..."
	timeout 120s env $(PYTHONPATH) $(PYTHON) test_basic_integration.py --standalone

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

# Run critical API tests that catch method compatibility issues
.PHONY: test-critical
test-critical: test-api test-integrity test-contract
	@echo "All critical API tests completed successfully"
.DEFAULT_GOAL := help
