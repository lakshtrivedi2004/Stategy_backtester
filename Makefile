PY ?= venv/bin/python
PIP ?= venv/bin/pip

.PHONY: setup backend frontend dev test build serve

setup:            ## install backend + frontend dependencies
	test -d venv || python3 -m venv venv
	$(PIP) install -r backend/requirements-dev.txt
	cd frontend && npm install

backend:          ## API with auto-reload on :8000
	cd backend && ../$(PY) -m uvicorn backtester.api:app --reload --port 8000

frontend:         ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

dev:              ## run both (Ctrl-C stops both)
	$(MAKE) -j2 backend frontend

test:
	cd backend && ../$(PY) -m pytest -q

build:            ## production frontend bundle (served by the API)
	cd frontend && npm run build

serve: build      ## single process: API + built site on :8000
	cd backend && ../$(PY) -m uvicorn backtester.api:app --host 0.0.0.0 --port 8000
