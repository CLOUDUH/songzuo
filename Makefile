.PHONY: build up down logs test test-web

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	python -m pytest backend/tests

test-web:
	pnpm test && pnpm build
