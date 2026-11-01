import pytest

from watchtower.models.config import (
    DEFAULT_MODEL,
    ModelConfigError,
    default_gateway,
    from_env,
    resolve_base_url,
)

# /proc/net/route as WSL2 NAT mode shows it: default route via 172.29.144.1 (observed 1 Nov).
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t01901DAC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t00901DAC\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0
"""


def test_default_gateway_decodes_little_endian_hex():
    assert default_gateway(ROUTE) == "172.29.144.1"


def test_no_default_route():
    assert default_gateway(ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[2]) is None


def test_auto_resolves_to_gateway_port_11434():
    assert resolve_base_url("auto", ROUTE) == "http://172.29.144.1:11434"
    assert resolve_base_url(None, ROUTE) == "http://172.29.144.1:11434"


def test_explicit_url_wins_and_is_normalised():
    assert resolve_base_url("http://localhost:11434/") == "http://localhost:11434"


def test_bad_values_rejected():
    with pytest.raises(ModelConfigError):
        resolve_base_url("localhost:11434")
    with pytest.raises(ModelConfigError):
        resolve_base_url("auto", "Iface\tDestination\tGateway\n")


def test_from_env_precedence():
    env = {"WATCHTOWER_MODEL_BASE_URL": "http://a:1", "WATCHTOWER_MODEL": "m1"}
    assert from_env(env).base_url == "http://a:1"
    assert from_env(env).model == "m1"
    assert from_env(env, base_url="http://b:2", model="m2").model == "m2"
    assert from_env({"WATCHTOWER_MODEL_BASE_URL": "http://a:1"}).model == DEFAULT_MODEL
    with pytest.raises(ModelConfigError):
        from_env({"WATCHTOWER_MODEL_BASE_URL": "http://a:1", "WATCHTOWER_MODEL_TIMEOUT_S": "x"})
