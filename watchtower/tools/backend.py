"""Backend seam between the tools and the cluster.

Tools never call kubectl themselves. They ask a Backend for RAW output (JSON dict or text) and
parse it into models. That is what makes telemetry capture/replay possible (W3 target 2):
a CapturingBackend can wrap KubectlBackend and store raw output, and a ReplayBackend can serve
it back, and the tools' parsing code runs identically in both cases.
"""

import json
import subprocess
from typing import Protocol

NAMESPACE = "watchtower"  # fixed on purpose: the agent never chooses a namespace
MAX_ERROR_CHARS = 500
MAX_LOG_TAIL = 1000
MAX_LOG_BYTES_FETCHED = 262144  # bound what we pull from the cluster, before tool-level capping
RAW_ALLOWED_PREFIXES = (
    f"/api/v1/namespaces/{NAMESPACE}/services/",  # http_probe via the API-server service proxy
    f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/",  # get_metrics, if metrics-server
)


class BackendError(Exception):
    """Raised when the backend cannot produce output (kubectl non-zero exit, timeout, ...)."""


class Backend(Protocol):
    def get_json(self, kind: str, name: str | None = None, selector: str | None = None) -> dict:
        """`kubectl get <kind> [name] [-l selector] -o json` (always a dict; lists have 'items')."""
        ...

    def logs(self, pod: str, container: str | None, previous: bool, tail: int) -> str:
        """Raw log text (last `tail` lines)."""
        ...

    def raw_get(self, path: str) -> str:
        """`kubectl get --raw <path>` for an allow-listed API path (http_probe, get_metrics)."""
        ...


class KubectlBackend:
    """Live backend: shells out to kubectl.

    Rules:
    - argv list only, never shell=True
    - always pass -n NAMESPACE
    - always set a subprocess timeout
    - arguments reaching here were already validated in args.py, but don't rely on that alone
    """

    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s

    def _run(self, argv: list[str]) -> str:
        """Run `kubectl <argv> -n NAMESPACE` and return stdout.

        Every failure mode is converted to BackendError so callers handle one exception type.
        """
        cmd = ["kubectl", *argv, "-n", NAMESPACE]  # a list, never a shell string
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,  # we inspect returncode ourselves to build a useful message
            )
        except subprocess.TimeoutExpired:
            raise BackendError(f"kubectl timed out after {self.timeout_s}s") from None
        except FileNotFoundError:
            raise BackendError("kubectl not found on PATH") from None

        if proc.returncode != 0:
            # stderr can echo attacker-influenced text (e.g. a pod name), so cap its size.
            # It is tagged UNTRUSTED later, when fail() wraps this message.
            detail = proc.stderr.strip()[:MAX_ERROR_CHARS]
            raise BackendError(f"kubectl exited {proc.returncode}: {detail}")

        return proc.stdout

    def get_json(self, kind: str, name: str | None = None, selector: str | None = None) -> dict:
        # Defence in depth: args.py validates upstream, but a value starting with '-' would be
        # read by kubectl as a flag, so refuse it here too.
        for value in (kind, name, selector):
            if value is not None and (not value or value.startswith("-")):
                raise BackendError(f"invalid argument: {value!r}")

        argv = ["get", kind]
        if name is not None:
            argv.append(name)
        if selector is not None:
            argv += ["-l", selector]
        argv += ["-o", "json"]

        out = self._run(argv)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise BackendError("kubectl returned invalid JSON") from None

    def logs(self, pod: str, container: str | None, previous: bool, tail: int) -> str:
        for value in (pod, container):
            if value is not None and (not value or value.startswith("-")):
                raise BackendError(f"invalid argument: {value!r}")
        if not 1 <= tail <= MAX_LOG_TAIL:
            raise BackendError(f"tail must be between 1 and {MAX_LOG_TAIL}")

        argv = ["logs", pod, "--tail", str(tail), f"--limit-bytes={MAX_LOG_BYTES_FETCHED}"]
        if container is not None:
            argv += ["-c", container]
        if previous:
            argv.append("--previous")
        return self._run(argv)

    def raw_get(self, path: str) -> str:
        # Only allow-listed API paths: service proxy (http_probe) and the metrics API.
        if ".." in path or not path.startswith(RAW_ALLOWED_PREFIXES):
            raise BackendError(f"path not allowed: {path[:80]!r}")
        return self._run(["get", "--raw", path])
