.PHONY: install test lint clean

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

lint:
	ruff check src tests

format:
	ruff format src tests

check:
	python -m compileall src

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist build .pytest_cache .ruff_cache
