from watchtower.tools.provenance import Trust, fail, ok, trusted, untrusted


def test_tag_constructors():
    a = trusted(5)
    assert (a.value, a.trust) == (5, Trust.TRUSTED)
    b = untrusted("web-1")
    assert (b.value, b.trust) == ("web-1", Trust.UNTRUSTED)


def test_ok_envelope():
    r = ok("get_pods", {"x": 1}, 3, truncated=True)
    assert r.ok and r.data == 3 and r.error is None and r.truncated is True
    assert ok("get_pods", {}, [1]).truncated is False


def test_fail_error_is_untrusted():
    # kubectl stderr can echo attacker-controlled text, so errors are never TRUSTED.
    r = fail("get_pods", {}, "boom")
    assert not r.ok and r.data is None
    assert r.error.trust == Trust.UNTRUSTED and r.error.value == "boom"


def test_json_round_trip():
    r = fail("get_pods", {}, "boom")
    assert type(r).model_validate_json(r.model_dump_json()) == r
