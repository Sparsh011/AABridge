.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PUBLIC_KEY ?= $(HOME)/Library/Application Support/AABridge/device-key.pub
PEER ?= macbook

.PHONY: help check build

help:
	@echo 'make build  Run checks and build the universal macOS app and ZIP in dist/'
	@echo 'make check  Run deterministic tests without model calls'
	@echo 'Override PYTHON, PUBLIC_KEY or PEER as needed; see docs/desktop.md.'

check:
	"$(PYTHON)" scripts/check.py

build: check
	@test -r "$(PUBLIC_KEY)" || { echo 'Pairing public key missing. Run make build PUBLIC_KEY="/absolute/path/to/key.pub" with an existing dedicated Ed25519 public key.' >&2; exit 1; }
	"$(PYTHON)" -m build --wheel --no-isolation
	@set -eu; \
	mkdir -p dist; \
	build_dir="$$(mktemp -d dist/AABridge-build-XXXXXX)"; \
	"$(PYTHON)" scripts/make_pairing_bundle.py \
		--public-key "$(PUBLIC_KEY)" --peer "$(PEER)" \
		--output "$$build_dir/SetupPayload.zip"; \
	"$(PYTHON)" scripts/build_desktop.py \
		--payload-zip "$$build_dir/SetupPayload.zip" \
		--output "$$build_dir/AABridge.zip"; \
	/usr/bin/ditto -x -k "$$build_dir/AABridge.zip" "$$build_dir"; \
	echo "Built app: $$build_dir/AABridge.app"; \
	echo "Share ZIP: $$build_dir/AABridge.zip"
