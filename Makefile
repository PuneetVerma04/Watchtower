CLUSTER_NAME := watchtower

.PHONY: cluster-up cluster-down build load deploy redeploy verify lint fmt

cluster-up:
	kind create cluster --name $(CLUSTER_NAME) --config cluster/kind-config.yaml

cluster-down:
	kind delete cluster --name $(CLUSTER_NAME)

build:
	docker build -f app/orders/Dockerfile -t orders:dev .
	docker build -f app/gateway/Dockerfile -t gateway:dev .
	docker build -f app/inventory/Dockerfile -t inventory:dev .

load: build
	kind load docker-image orders:dev gateway:dev inventory:dev --name $(CLUSTER_NAME)

deploy:
	kubectl apply -f cluster/manifests/namespace.yaml
	kubectl config set-context --current --namespace=watchtower
	kubectl create secret generic postgres-credentials -n watchtower --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -f cluster/manifests/postgres-statefulset.yaml -f cluster/manifests/postgres-service.yaml
	kubectl apply -f cluster/manifests/redis-statefulset.yaml -f cluster/manifests/redis-service.yaml
	kubectl wait --for=condition=Ready pod -l app=postgres -n watchtower --timeout=60s
	kubectl wait --for=condition=Ready pod -l app=redis -n watchtower --timeout=60s
	kubectl apply -f cluster/manifests/orders-deployment.yaml -f cluster/manifests/orders-service.yaml
	kubectl apply -f cluster/manifests/gateway-deployment.yaml -f cluster/manifests/gateway-service.yaml
	kubectl apply -f cluster/manifests/inventory-deployment.yaml -f cluster/manifests/inventory-service.yaml
	kubectl rollout restart deployment/orders deployment/gateway deployment/inventory -n watchtower
	kubectl wait --for=condition=Ready pod -l app=orders -n watchtower --timeout=60s
	kubectl wait --for=condition=Ready pod -l app=gateway -n watchtower --timeout=60s
	kubectl wait --for=condition=Ready pod -l app=inventory -n watchtower --timeout=60s

verify:
	kubectl port-forward svc/gateway 8000:8000 -n watchtower & \
	GW_PID=$$!; \
	kubectl port-forward svc/inventory 8001:8000 -n watchtower & \
	INV_PID=$$!; \
	sleep 2; \
	curl -f http://localhost:8000/healthz; echo; \
	curl -f http://localhost:8001/healthz; echo; \
	curl -f -s http://localhost:8001/check/verify-smoke-test; echo; \
	RESPONSE=$$(curl -f -s -X POST http://localhost:8000/orders \
		-H "Content-Type: application/json" \
		-d '{"item": "verify-smoke-test", "quantity": 1}'); \
	echo "$$RESPONSE"; \
	echo "$$RESPONSE" | grep -q '"order_id"' \
		&& echo "orders round-trip OK" \
		|| (echo "orders round-trip FAILED"; kill $$GW_PID $$INV_PID; exit 1); \
	kill $$GW_PID $$INV_PID

redeploy: load deploy verify

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
