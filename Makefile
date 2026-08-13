PYTHON ?= python3

.PHONY: test test-ops compose-check preflight-production

test:
	$(PYTHON) tests/test_core.py
	$(PYTHON) tests/test_retrieval.py
	$(PYTHON) tests/test_auth.py
	$(PYTHON) tests/test_cookie_gate.py
	$(PYTHON) tests/test_deploy_tools.py

test-ops:
	$(PYTHON) tests/test_deploy_tools.py

compose-check:
	docker compose --env-file .env.example config --no-env-resolution --quiet

preflight-production:
	$(PYTHON) scripts/deploy_preflight.py --mode production --env-file .env --min-docs 100
