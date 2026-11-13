.PHONY: install dev test lint backtest dashboard site docker

install:
	pip install -e .

dev:
	pip install -e ".[dev,dashboard,llm]"

test:
	pytest

lint:
	ruff check src tests

backtest:
	riskaware backtest --compare buy_and_hold,ema_bounce --open

dashboard:
	riskaware dashboard

site:
	python -m http.server 8000 --directory site

docker:
	docker compose -f docker/docker-compose.yml up -d
