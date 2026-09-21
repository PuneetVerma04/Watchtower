CLUSTER_NAME := watchtower

.PHONY: cluster-up cluster-down build load deploy redeploy verify lint fmt

cluster-up:
	kind create cluster --name $(CLUSTER_NAME) --config cluster/kind-config.yaml

cluster-down:
	kind delete cluster --name $(CLUSTER_NAME)

build:
	docker build -f app/orders/Dockerfile -t orders:dev .
	docker build -f app/gateway/Dockerfile -t gateway:dev .

load: build
	kind load docker-image orders:dev gateway:dev --name $(CLUSTER_NAME)

deploy:
	kubectl apply -f cluster/manifests/namespace.yaml
	kubectl config set-context --current --namespace=watchtower
	kubectl create secret generic postgres-credentials -n watchtower --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -f cluster/manifests/postgres-statefulset.yaml -f cluster/manifests/postgres-service.yaml
	kubectl wait --for=condition=Ready pod -l app=postgres -n watchtower --timeout=60s
	kubectl apply -f cluster/manifests/orders-deployment.yaml -f cluster/manifests/orders-service.yaml
	kubectl apply -f cluster/manifests/gateway-deployment.yaml -f cluster/manifests/gateway-service.yaml
	kubectl rollout restart deployment/orders deployment/gateway -n watchtower
	kubectl wait --for=condition=Ready pod -l app=orders -n watchtower --timeout=60s
	kubectl wait --for=condition=Ready pod -l app=gateway -n watchtower --timeout=60s

verify:
	kubectl port-forward svc/gateway 8000:8000 -n watchtower & \
	PF_PID=$$!; \
	sleep 2; \
	curl -f http://localhost:8000/healthz; echo; \
	RESPONSE=$$(curl -f -s -X POST http://localhost:8000/orders \
		-H "Content-Type: application/json" \
		-d '{"item": "verify-smoke-test", "quantity": 1}'); \
	echo "$$RESPONSE"; \
	echo "$$RESPONSE" | grep -q '"order_id"' \
		&& echo "orders round-trip OK" \
		|| (echo "orders round-trip FAILED"; kill $$PF_PID; exit 1); \
	kill $$PF_PID

redeploy: load deploy verify

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
