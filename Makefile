.PHONY: help check test lint fmt install

help:
	@echo "make install  - editable install of the desktop client"
	@echo "make test     - run the test suite"
	@echo "make lint     - run ruff"
	@echo "make fmt      - run ruff format"
	@echo "make check    - lint + test"

install:
	python -m pip install -e 'desktop[dev]'

test:
	python -m pytest

lint:
	python -m ruff check .

fmt:
	python -m ruff format .

check: lint test
