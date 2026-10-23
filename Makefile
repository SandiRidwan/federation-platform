# One entry point. On Windows without GNU make, run the same thing with:
#     .venv\Scripts\python -m tools.tasks.verify
#
# PYTHON defaults to whatever is on PATH; point it at the project venv when running `make` from a
# shell that has not activated it:
#     make verify PYTHON=.venv/Scripts/python.exe

PYTHON ?= python
COMPOSE ?= docker compose

.PHONY: help up down logs install verify verify-fast reports clean

help:
	@echo "make up          start Trino, Postgres, S3 and Druid, then wait until they answer"
	@echo "make install     install requirements into the active interpreter"
	@echo "make verify      run every component and check every report (M10)"
	@echo "make verify-fast skip the steps that need the compose stack"
	@echo "make reports     list the verdict of every report"
	@echo "make down        stop the stack"

up:
	$(COMPOSE) up -d
	@echo "waiting for Trino..."
	$(PYTHON) -m federation.trino.catalog --wait
	@echo "waiting for Druid..."
	$(PYTHON) -m federation.druid.ingest --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --tail=100

install:
	$(PYTHON) -m pip install -r requirements.txt

verify:
	$(PYTHON) -m tools.tasks.verify

verify-fast:
	$(PYTHON) -m tools.tasks.verify --skip-docker

reports:
	@$(PYTHON) -c "import json,pathlib; [print(f'{p.name:<30} {json.loads(p.read_text()).get(\"result\", \"-\")}') for p in sorted(pathlib.Path('quality/reports').glob('*.json'))]"

clean:
	$(PYTHON) -c "import shutil,pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]"
