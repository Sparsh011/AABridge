.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PUBLIC_KEY ?= $(HOME)/Library/Application Support/AABridge/device-key.pub
PEER ?= macbook
APP_PATH ?= build/AABridge.app

.PHONY: help check build

help:
	@echo 'make build  Check and replace build/AABridge.app (no ZIPs)'
	@echo 'make check  Run deterministic tests without model calls'
	@echo 'Override APP_PATH, PYTHON, PUBLIC_KEY or PEER; see docs/desktop.md.'

check:
	"$(PYTHON)" scripts/check.py

build: check
	"$(PYTHON)" scripts/build_app.py --app-path "$(APP_PATH)" \
		--public-key "$(PUBLIC_KEY)" --peer "$(PEER)"
