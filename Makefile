# Makefile for Raspilapse development

.PHONY: help format check test test-cov lint clean install dev-install

help:
	@echo "Raspilapse Development Commands"
	@echo ""
	@echo "  make format      - Format code with Black (RUN BEFORE COMMIT!)"
	@echo "  make check       - Check if code is formatted correctly"
	@echo "  make test        - Run all tests"
	@echo "  make test-cov    - Run tests with coverage report"
	@echo "  make lint        - Run flake8 linter"
	@echo "  make all         - Format, check, and test (recommended before commit)"
	@echo "  make install     - Install production dependencies"
	@echo "  make dev-install - Install development dependencies"
	@echo "  make clean       - Remove cache and temp files"

format:
	@echo "🎨 Formatting code with Black..."
	black src/ tests/ --line-length=100

check:
	@echo "✅ Checking code formatting..."
	black --check src/ tests/ --line-length=100

test:
	@echo "🧪 Running tests..."
	python3 -m pytest tests/ -v

test-cov:
	@echo "📊 Running tests with coverage..."
	python3 -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=xml

lint:
	@echo "🔍 Linting code..."
	flake8 src/ --max-line-length=100 --count --statistics

all: format check test
	@echo "✅ All checks passed! Ready to commit."

install:
	@echo "📦 Installing production dependencies..."
	pip3 install -r requirements.txt

dev-install:
	@echo "🛠️  Installing development dependencies..."
	pip3 install -r requirements-dev.txt

clean:
	@echo "🧹 Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	find . -type f -name "coverage.xml" -delete
	@echo "✨ Clean!"
