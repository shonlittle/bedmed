.DEFAULT_GOAL := help
.PHONY: help up down logs reset chat trace test eval lint format

help: ## Show the available commands
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

up: ## Build and start the database, API and web UI (http://localhost:8080)
	docker compose up --build --detach --wait
	@echo "BedMed UI: http://localhost:8080   API docs: http://localhost:8000/docs"

down: ## Stop everything and discard the database
	docker compose down

logs: ## Follow the API and web UI logs
	docker compose logs --follow api web

reset: ## Reset the database to the seed data
	docker compose run --rm api python -m api.seed

chat: ## Chat with the assistant in the terminal
	docker compose run --rm assistant

trace: ## Terminal chat, showing the workflow path for each turn
	docker compose run --rm assistant python -m assistant --trace

test: ## Run the test suite
	docker compose run --rm api pytest

eval: ## Score extraction and policy retrieval against labelled cases (calls the models)
	docker compose run --rm assistant python -m assistant.evaluate
	docker compose run --rm assistant python -m assistant.evaluate_policy

lint: ## Check formatting and lint rules
	docker compose run --rm api sh -c "ruff format --check . && ruff check ."

format: ## Apply formatting
	docker compose run --rm api ruff format .
