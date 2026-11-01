import pytest
from pydantic import ValidationError

from watchtower.tools.args import (
    GetPodLogsArgs,
    GetPodsArgs,
    HttpProbeArgs,
)

HOSTILE_NAMES = [
    "--all-namespaces",
    "-n",
    "-o=yaml",
    "../x",
    "a..b",
    "a b",
    "a;rm -rf /",
    "UPPER",
    "a" * 254,
    "",
    "name\nnewline",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_pod_names_rejected(name):
    with pytest.raises(ValidationError):
        GetPodsArgs(name=name)
    with pytest.raises(ValidationError):
        GetPodLogsArgs(pod=name)


@pytest.mark.parametrize("name", ["worker", "worker-786756b787-2dhpg", "postgres-0", "a.b-c"])
def test_valid_names_accepted(name):
    assert GetPodsArgs(name=name).name == name


@pytest.mark.parametrize("selector", ["-o=yaml", "app", "a=b,", "x=y;z", "a=b c", "=b", "a=b" * 12])
def test_bad_selectors_rejected(selector):
    with pytest.raises(ValidationError):
        GetPodsArgs(selector=selector)


@pytest.mark.parametrize(
    "selector",
    ["app=worker", "app=worker,tier=backend", "a.b/c=d_e", "app="],  # empty value is valid
)
def test_good_selectors_accepted(selector):
    assert GetPodsArgs(selector=selector).selector == selector


def test_namespace_and_unknown_keys_rejected():
    # The model must not be able to smuggle a namespace (or anything else) past validation.
    with pytest.raises(ValidationError):
        GetPodsArgs.model_validate({"namespace": "kube-system"})
    with pytest.raises(ValidationError):
        GetPodLogsArgs.model_validate({"pod": "worker", "all_namespaces": True})


@pytest.mark.parametrize("tail", [0, -1, 201, 10**9])
def test_tail_lines_bounded(tail):
    with pytest.raises(ValidationError):
        GetPodLogsArgs(pod="worker", tail_lines=tail)


@pytest.mark.parametrize("path", ["healthz", "/../secrets", "/a b", "/x?y=z", "/" + "a" * 200])
def test_http_path_restricted(path):
    with pytest.raises(ValidationError):
        HttpProbeArgs(service="gateway", port=8000, path=path)


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_http_port_bounded(port):
    with pytest.raises(ValidationError):
        HttpProbeArgs(service="gateway", port=port)
