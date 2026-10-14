.PHONY: all demo test soak finops package clean help

# Auto-detect Python executable (venv or system)
PYTHON ?= $(shell if [ -f "../ferrox-py/venv/Scripts/python.exe" ]; then echo "../ferrox-py/venv/Scripts/python.exe"; \
            elif [ -f "venv/bin/python" ]; then echo "venv/bin/python"; \
            elif [ -f ".venv/bin/python" ]; then echo ".venv/bin/python"; \
            elif command -v python3 >/dev/null 2>&1; then echo "python3"; \
            else echo "python"; fi)

all: demo

help:
	@echo "================================================================================"
	@echo " ferrox-py-aspyre: Enterprise Financial Integration Engine"
	@echo "================================================================================"
	@echo "  make demo     - Complete turnkey evaluation (Docker + Pytest + Chaos + FinOps)"
	@echo "  make test     - Run 26-test suite (Unit, Integration, Upstream Chaos)"
	@echo "  make soak     - Execute 1,000-tenant DRR Fair-Queue Live Chaos Soak benchmark"
	@echo "  make finops   - Display AWS FinOps Unit Economics comparison table"
	@echo "  make package  - Build clean distribution zip (ferrox-py-aspyre-enterprise.zip)"
	@echo "  make clean    - Remove bytecode caches, test logs, and temporary packages"
	@echo "================================================================================"

demo:
	@$(PYTHON) scripts/run_demo.py demo

test:
	@$(PYTHON) scripts/run_demo.py test

soak:
	@$(PYTHON) scripts/run_demo.py soak

finops:
	@$(PYTHON) scripts/run_demo.py finops

package:
	@$(PYTHON) scripts/run_demo.py package

clean:
	@rm -rf __pycache__ .pytest_cache ferrox-py-aspyre-enterprise.zip
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
