# Pulse developer commands. Run `make help` for the list.
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

ENV ?= dev
IMAGE ?= pulse:local
TARGET_PORT ?= 18080
COMPOSE := docker compose
TF_DIR := infra/envs/$(ENV)
AWS_PROFILE ?= pulse
# Only the targets that talk to AWS get the SSO profile; tests and the local stack must not
# (botocore fails if the profile doesn't exist yet).
AWS_TARGETS := infra-init infra-plan infra-up kubeconfig render-values argocd-bootstrap infra-down
$(AWS_TARGETS): export AWS_PROFILE := $(AWS_PROFILE)

# Tests talk to the compose Postgres (host port 5433) and ElasticMQ.
TEST_ENV := PULSE_DB_HOST=localhost PULSE_DB_PORT=$${PULSE_LOCAL_PG_PORT:-5433} \
	PULSE_DB_USER=postgres PULSE_DB_PASSWORD=postgres PULSE_SQS_ENDPOINT_URL=http://localhost:9324

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- Phase 1: code quality ---------------------------------------------------------------
.PHONY: fmt lint test test-unit
fmt: ## Format Python (ruff) and Terraform
	cd app && uv run ruff format . && uv run ruff check --fix .
	terraform fmt -recursive infra

lint: ## Lint Python (ruff check + format check) and Terraform formatting
	cd app && uv run ruff check . && uv run ruff format --check .
	terraform fmt -check -recursive infra

test: ## Run all tests with coverage (starts Postgres + ElasticMQ if needed)
	$(COMPOSE) up -d --wait postgres elasticmq
	cd app && $(TEST_ENV) uv run pytest --cov --cov-report=term-missing

test-unit: ## Run unit tests only (no containers)
	cd app && uv run pytest tests/unit

# ---- Phase 2: local stack ----------------------------------------------------------------
.PHONY: build up down logs seed target-down target-up scan
build: ## Build the container image ($(IMAGE))
	docker build -t $(IMAGE) .

up: ## Start the full local stack (http://localhost:8000)
	$(COMPOSE) up -d --build --wait
	@echo "Status page: http://localhost:8000   Prometheus: http://localhost:9090"

down: ## Stop the local stack and delete its volumes
	$(COMPOSE) down -v --remove-orphans

logs: ## Follow logs of the app services
	$(COMPOSE) logs -f api scheduler worker target

seed: ## Create demo monitors on the local stack
	python3 local/seed.py

target-down: ## Flip the chaos target's /switch endpoint to 503
	curl -fsS -X POST http://localhost:$(TARGET_PORT)/switch/down && echo

target-up: ## Flip the chaos target's /switch endpoint back to 200
	curl -fsS -X POST http://localhost:$(TARGET_PORT)/switch/up && echo

scan: build ## Trivy scan of the local image (fixable HIGH/CRITICAL)
	trivy image --severity HIGH,CRITICAL --ignore-unfixed $(IMAGE)

# ---- Phase 3: infrastructure (plan is read-only; up/down change AWS and cost money) -----
.PHONY: infra-init infra-plan infra-up kubeconfig render-values
infra-init:
	terraform -chdir=$(TF_DIR) init -input=false

infra-plan: infra-init ## terraform plan for ENV (read-only)
	terraform -chdir=$(TF_DIR) plan -input=false -var-file=$(ENV).tfvars

infra-up: infra-init ## Plan, show, ask for confirmation, then apply ENV
	terraform -chdir=$(TF_DIR) plan -input=false -var-file=$(ENV).tfvars -out=$(ENV).tfplan
	@read -r -p "Apply this plan to '$(ENV)'? This creates billable AWS resources. Type 'yes': " ans; \
	  if [[ "$$ans" != "yes" ]]; then echo "Aborted."; rm -f $(TF_DIR)/$(ENV).tfplan; exit 1; fi
	terraform -chdir=$(TF_DIR) apply -input=false $(ENV).tfplan
	rm -f $(TF_DIR)/$(ENV).tfplan
	./scripts/render-infra-values.sh $(ENV)

kubeconfig: ## Point kubectl at ENV's EKS cluster
	aws eks update-kubeconfig --name pulse-$(ENV) \
	  --region $$(terraform -chdir=$(TF_DIR) output -raw region) --alias pulse-$(ENV)

render-values: ## Write pulse-gitops/envs/ENV/infra-values.yaml from terraform outputs
	./scripts/render-infra-values.sh $(ENV)

# ---- Phase 5: GitOps and teardown ----------------------------------------------------------
.PHONY: argocd-bootstrap infra-down
argocd-bootstrap: ## Install Argo CD and the root app on ENV's cluster
	./scripts/argocd-bootstrap.sh $(ENV)

infra-down: infra-init ## Delete k8s-created AWS resources, then terraform destroy ENV
	@read -r -p "Destroy environment '$(ENV)'? Type the env name to confirm: " ans; \
	  if [[ "$$ans" != "$(ENV)" ]]; then echo "Aborted."; exit 1; fi
	./scripts/pre-destroy-cleanup.sh $(ENV)
	terraform -chdir=$(TF_DIR) destroy -input=false -var-file=$(ENV).tfvars

# ---- Phase 5-6: UIs (port-forwards; nothing is exposed publicly) -------------------------
.PHONY: argocd-ui grafana prometheus
argocd-ui: ## Argo CD UI on https://localhost:8443
	@echo "admin password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
	kubectl -n argocd port-forward svc/argocd-server 8443:443

grafana: ## Grafana on http://localhost:3000
	kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80

prometheus: ## Prometheus on http://localhost:9090
	kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090

# ---- Phase 7: load tests (only against endpoints you own) --------------------------------
.PHONY: loadtest-read loadtest-storm loadtest-storm-clean
BASE_URL ?= http://localhost:8000
TARGET_BASE ?= http://pulse-target:8080

loadtest-read: ## k6 Scenario A: 50 VUs reading the status page + stats for 10 min
	k6 run -e BASE_URL=$(BASE_URL) loadtest/api_read.js

loadtest-storm: ## Scenario B: create 2,000 monitors against pulse-target (30s interval)
	python3 loadtest/seed_monitors.py --api-url $(BASE_URL) --target-url $(TARGET_BASE)

loadtest-storm-clean: ## Delete the Scenario B monitors
	python3 loadtest/seed_monitors.py --api-url $(BASE_URL) --delete
