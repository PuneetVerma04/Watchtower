"""Held-out cases for the W4 tool-calling gate, written after the first live run.

The first run (b59342d, report in results/reports/) missed two hard thresholds. Before changing
anything about how the tool schemas are presented to the model, these cases were committed so the
fix cannot be tuned to the 30 pre-registered cases alone. They probe the same failure classes the
first run exposed, on different tools, objects and phrasings:

  optional name argument left out or renamed   (C1/C2: "list all" vs "just this one")
  invented or out-of-range arguments           (C4: another namespace, unbounded log request)
  recovery from an `invalid arguments` result  (C5: bad name, invented field, malformed path)

Rules for this file:
- It is reported separately and never pooled into the gate metrics: the gate stays the 30 cases
  and thresholds committed in 0a146b2.
- Cases are frozen once committed. Do not edit a case after seeing how a model does on it; add a
  new case instead.
- Same conversation shape and scoring as cases.py.
"""

from watchtower.eval.tool_calling.cases import (
    CH,
    GATEWAY,
    NO_CALL,
    NO_INVALID_CALL,
    ORDERS,
    RC,
    A,
    Call,
    Case,
    R,
    S,
    User,
    _call,
)

HELDOUT_CASES: tuple[Case, ...] = (
    # ------------------------------------------------------------------ C1 selection
    Case(
        "h1-list-services",
        S,
        "healthy",
        (User("Which Services exist in the namespace?"),),
        _call("get_service", name=None),
    ),
    Case(
        "h1-list-deployments",
        S,
        "healthy",
        (User("Give me an overview of every deployment: replicas and images."),),
        _call("get_deployment", name=None),
    ),
    # ------------------------------------------------------------------ C2 arg fidelity
    Case(
        "h2-one-pod",
        A,
        "healthy",
        (User(f"I only care about the pod named {GATEWAY}. Show its status, nothing else."),),
        _call("get_pods", name=GATEWAY),
    ),
    Case(
        "h2-one-service",
        A,
        "healthy",
        (User("Show me only the gateway Service, not the other Services."),),
        _call("get_service", name="gateway"),
    ),
    Case(
        "h2-logs-count",
        A,
        "healthy",
        (User(f"Show the last 25 log lines of the running container in pod {ORDERS}."),),
        _call("get_pod_logs", pod=ORDERS, previous=False, tail_lines=25),
    ),
    # ------------------------------------------------------------------ C3 chaining
    Case(
        "h3-orders-logs",
        CH,
        "healthy",
        (
            User("Something is slow in orders. Start by finding its pod."),
            Call("get_pods", {"selector": "app=orders"}),
            User("Show the recent logs of that pod."),
        ),
        _call("get_pod_logs", pod=ORDERS),
    ),
    # ------------------------------------------------------------------ C4 restraint
    Case(
        "h4-other-namespace-deployments",
        R,
        "healthy",
        (User("Show the deployments in the production namespace."),),
        NO_INVALID_CALL,
    ),
    Case(
        "h4-whole-log-history",
        R,
        "healthy",
        (User(f"Dump the entire log history of pod {GATEWAY}, every line it has ever written."),),
        NO_INVALID_CALL,
    ),
    Case(
        "h4-answer-in-context-service",
        R,
        "healthy",
        (
            User("Check the services."),
            Call("get_service", {}),
            User("From that result, what type of Service is gateway? One word."),
        ),
        NO_CALL,
    ),
    # ------------------------------------------------------------------ C5 recovery
    Case(
        "h5-describe-bad-name",
        RC,
        "healthy",
        (
            User("Describe the orders pod."),
            Call("get_pods", {"selector": "app=orders"}),
            Call("describe_pod", {"pod": "Orders Pod"}),
        ),
        _call("describe_pod", pod=ORDERS),
    ),
    Case(
        "h5-deployment-namespace",
        RC,
        "healthy",
        (
            User("Show the orders deployment."),
            Call("get_deployment", {"name": "orders", "namespace": "watchtower"}),
        ),
        _call("get_deployment", name="orders"),
    ),
    Case(
        "h5-probe-path",
        RC,
        "healthy",
        (
            User("Probe the orders service on port 8000 at its /healthz path."),
            Call("http_probe", {"service": "orders", "port": 8000, "path": "healthz"}),
        ),
        _call("http_probe", service="orders", port=8000, path="/healthz"),
    ),
)
