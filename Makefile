SHELL := /bin/bash
PYTHON ?= python3

.PHONY: help install-python install-web dev-api dev-web dev-financial test test-python test-web test-financial lint typecheck quality

help:
	@echo "GlobeMind developer commands"
	@echo "  make install-python   Install API and test dependencies"
	@echo "  make install-web      Install maintained frontend dependencies"
	@echo "  make dev-api          Start the local FastAPI development server"
	@echo "  make dev-web          Start the Vue development server"
	@echo "  make dev-financial    Start the financial terminal"
	@echo "  make test             Run offline Python and frontend tests"
	@echo "  make quality          Run the complete offline quality gate"

install-python:
	$(PYTHON) -B -m pip install --disable-pip-version-check -r requirements-dev.txt

install-web:
	npm ci --no-audit --no-fund --prefix frontend/vue_project
	npm ci --no-audit --no-fund --prefix frontend/financial-terminal

dev-api:
	cd backend && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m uvicorn api.main:app --host 127.0.0.1 --port 8088 --reload

dev-web:
	npm --prefix frontend/vue_project run dev:main

dev-financial:
	npm --prefix frontend/financial-terminal run dev

test: test-python test-web test-financial

test-python:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m pytest -q -m "not integration and not live_db and not gpu and not slow"

test-web:
	npm --prefix frontend/vue_project run test:features

test-financial:
	npm --prefix frontend/financial-terminal run test:trust

lint:
	npm --prefix frontend/vue_project run lint

typecheck:
	npm --prefix frontend/financial-terminal run typecheck

quality:
	PYTHONDONTWRITEBYTECODE=1 PYTHON_BIN=$(PYTHON) deploy/run_quality_gate.sh --output /tmp/globemind-quality-gate.json
