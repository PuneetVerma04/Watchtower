CLUSTER_NAME := watchtower

.PHONY: cluster-up cluster-down lint fmt

cluster-up:
	kind create cluster --name $(CLUSTER_NAME) --config cluster/kind-config.yaml

cluster-down:
	kind delete cluster --name $(CLUSTER_NAME)

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
