.PHONY: help test test-pure lint format format-check i18n distrib increment verify verify-pure clean profile-path

PLUGIN_DIR := sigate
VERSION := $(shell grep '^version=' $(PLUGIN_DIR)/metadata.txt | cut -d= -f2)
ZIP_NAME := sigate-$(VERSION).zip

help:
	@echo "SIGate development targets:"
	@echo "  make test          - full test suite (needs a real QGIS install for ui/ tests)"
	@echo "  make test-pure     - only the tests with no Qt/QGIS dependency (gateways/, download/, sources/, plus any ui/ test that happens to need no real widget)"
	@echo "  make lint          - ruff check"
	@echo "  make format        - ruff format (rewrites files)"
	@echo "  make format-check  - ruff format --check (fails if anything would be reformatted, no rewrite)"
	@echo "  make i18n          - regenerate i18n/sigate_en.ts from source"
	@echo "  make distrib       - build $(ZIP_NAME)"
	@echo "  make increment     - bump metadata.txt's version (X.Y.Z -> X.Y.Z+1), the one-bump-per-delivery convention docs/dev_workflow.md tracks"
	@echo "  make verify        - lint + format-check + test + distrib, then re-extract the built zip fresh and confirm it's actually correct (needs a real QGIS install for the full test suite)"
	@echo "  make verify-pure   - same as verify, but using test-pure instead of test - for an environment with no real QGIS install (e.g. CI, a sandbox)"
	@echo "  make clean         - remove __pycache__/.ruff_cache/.pytest_cache and any built zip"
	@echo "  make profile-path  - print where your QGIS profile's plugins folder likely is, for local install"

test:
	python3 -m pytest $(PLUGIN_DIR)/tests/

# The subset that never imports qgis/PyQt at all - runs anywhere, including
# a sandbox or CI runner with no real QGIS install. This is a real,
# maintained list (see docs/dev_workflow.md), not a heuristic guess - a
# ui/ file that only imports Qt classes inside function bodies (deferred,
# never at module import time) is pure-Python-safe too and can stay off
# this ignore list; check before adding a new ui/ test file here.
test-pure:
	python3 -m pytest $(PLUGIN_DIR)/tests/ -q \
		--ignore=$(PLUGIN_DIR)/tests/test_settings_dialog.py \
		--ignore=$(PLUGIN_DIR)/tests/test_target_picker.py \
		--ignore=$(PLUGIN_DIR)/tests/test_ui_settings.py \
		--ignore=$(PLUGIN_DIR)/tests/test_wfs_widget.py \
		--ignore=$(PLUGIN_DIR)/tests/test_bulk_listing_widget.py \
		--ignore=$(PLUGIN_DIR)/tests/test_connection_manager.py \
		--ignore=$(PLUGIN_DIR)/tests/test_download_flow.py \
		--ignore=$(PLUGIN_DIR)/tests/test_expression_builder.py \
		--ignore=$(PLUGIN_DIR)/tests/test_icons.py \
		--ignore=$(PLUGIN_DIR)/tests/test_wmts_wms_widget.py \
		--ignore=$(PLUGIN_DIR)/tests/test_download_progress_dialog.py \
		--ignore=$(PLUGIN_DIR)/tests/test_wmts_zoom_level_dialog.py \
		--ignore=$(PLUGIN_DIR)/tests/test_download_task.py \
		--ignore=$(PLUGIN_DIR)/tests/test_wmts_export_task.py \
		--ignore=$(PLUGIN_DIR)/tests/test_wmts_export_dialog.py \
		--ignore=$(PLUGIN_DIR)/tests/test_layer_groups.py \
		--ignore=$(PLUGIN_DIR)/tests/test_arcgis_rest_widget.py

lint:
	ruff check $(PLUGIN_DIR)/

format:
	ruff format $(PLUGIN_DIR)/

format-check:
	ruff format --check $(PLUGIN_DIR)/

i18n:
	pylupdate5 $(PLUGIN_DIR)/*.py $(PLUGIN_DIR)/ui/*.py -ts $(PLUGIN_DIR)/i18n/sigate_en.ts || \
	pylupdate6 $(PLUGIN_DIR)/*.py $(PLUGIN_DIR)/ui/*.py -ts $(PLUGIN_DIR)/i18n/sigate_en.ts

distrib:
	rm -f sigate-0.0.*.zip
	python3 -m compileall -q $(PLUGIN_DIR)/
	zip -rq $(ZIP_NAME) $(PLUGIN_DIR)/ \
		-x "*.pyc" -x "*__pycache__*" -x "*.ruff_cache*" -x "*.pytest_cache*"
	unzip -t $(ZIP_NAME) > /dev/null
	@echo "Built $(ZIP_NAME)"

# Bumps metadata.txt's version=X.Y.Z to X.Y.Z+1 (patch/build number only -
# this project has never needed a minor/major bump, matching the "one
# bump per delivered zip" convention docs/dev_workflow.md tracks).
# Python, not sed -i: BSD sed (macOS, this project's own dev machine) and
# GNU sed (Linux/CI) take -i differently (BSD requires an explicit,
# possibly-empty backup-suffix argument; GNU doesn't) - a real, common
# portability trap this sidesteps entirely by using something already a
# hard dependency of every other target in this file instead.
increment:
	@python3 -c "import pathlib; \
p = pathlib.Path('$(PLUGIN_DIR)/metadata.txt'); \
lines = p.read_text().splitlines(keepends=True); \
idx = next(i for i, l in enumerate(lines) if l.startswith('version=')); \
old = lines[idx].strip().split('=', 1)[1]; \
major, minor, patch = old.split('.'); \
new = f'{major}.{minor}.{int(patch) + 1}'; \
lines[idx] = f'version={new}\n'; \
p.write_text(''.join(lines)); \
print(f'version: {old} -> {new}')"

# The real "rebuild, extract fresh, re-test against the actual zip" cycle -
# every delivery this project has made was verified this way by hand
# before being handed off; this formalizes it as one command.
verify: lint format-check test distrib
	rm -rf .verify-extract
	mkdir -p .verify-extract
	unzip -q $(ZIP_NAME) -d .verify-extract
	python3 -m compileall -q .verify-extract/$(PLUGIN_DIR)/
	rm -rf .verify-extract
	@echo "verify: OK - $(ZIP_NAME) built, extracted fresh, and re-compiled clean"

# Same cycle, but for an environment with no real QGIS install at all
# (confirmed a real, genuine gap while building this Makefile: this
# environment has no qgis module, so the full `test` target's ui/
# collection fails outright at import time - test-pure exists specifically
# for this case).
verify-pure: lint format-check test-pure distrib
	rm -rf .verify-extract
	mkdir -p .verify-extract
	unzip -q $(ZIP_NAME) -d .verify-extract
	python3 -m compileall -q .verify-extract/$(PLUGIN_DIR)/
	rm -rf .verify-extract
	@echo "verify-pure: OK - $(ZIP_NAME) built, extracted fresh, and re-compiled clean (pure-Python tests only - no real QGIS install was used)"

clean:
	find $(PLUGIN_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .pytest_cache $(PLUGIN_DIR)/.ruff_cache $(PLUGIN_DIR)/.pytest_cache
	rm -f sigate-0.0.*.zip
	rm -rf .verify-extract

profile-path:
	@echo "QGIS profile plugin directories are typically:"
	@echo "  Linux:   ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/"
	@echo "  macOS:   ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/"
	@echo "  Windows: %APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\"
	@echo "For local development, symlink (not copy) the sigate/ folder there so edits take effect without repackaging."
