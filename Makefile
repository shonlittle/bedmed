.DEFAULT_GOAL := help
.PHONY: help up down logs reset chat trace test lint format

help: ## Show the available commands
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

up: ## Build and start the API and database in the background
	docker compose up --build --detach --wait api

down: ## Stop everything and discard the database
	docker compose down

logs: ## Follow the API logs
	docker compose logs --follow api

reset: ## Reset the database to the seed data
	docker compose run --rm api python -m api.seed

chat: ## Chat with the assistant
	docker compose run --rm assistant

trace: ## Chat with the assistant, showing the workflow path for each turn
	docker compose run --rm assistant python -m assistant --trace

test: ## Run the test suite
	docker compose run --rm api pytest

lint: ## Check formatting and lint rules
	docker compose run --rm api sh -c "ruff format --check . && ruff check ."

format: ## Apply formatting
	docker compose run --rm api ruff format .
