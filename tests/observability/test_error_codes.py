"""Catalog and classifier tests (design 39.5.2d sections 3, 5, 10)."""
import socket
import ssl

import pytest

from core.errors import GENERIC_CODE, AlemsError
from core.observability import error_catalog as ec


@pytest.fixture(autouse=True)
def _fresh_catalog():
    # every test sees the committed catalog, never a cached edited copy
    ec.reset_cache()
    yield
    ec.reset_cache()


class _HTTPErr(Exception):
    """Stand in for a client library error carrying a status."""

    def __init__(self, status):
        super().__init__("http %d" % status)
        self.status_code = status


def test_catalog_loads_and_has_generic():
    cat = ec.load()
    assert cat["catalog_version"]
    assert GENERIC_CODE in cat["codes"]


def test_tier1_alems_error_wins_over_site_code():
    exc = AlemsError("x", code="ALEMS-PERS-0105")
    assert ec.classify(exc, site_code="ALEMS-ETL-0101") == "ALEMS-PERS-0105"


def test_tier2_site_code_beats_rules():
    assert ec.classify(TimeoutError("t"), site_code="ALEMS-ETL-0103") == "ALEMS-ETL-0103"


@pytest.mark.parametrize("exc,code", [
    (TimeoutError("t"), "ALEMS-NET-0004"),
    (ConnectionResetError("r"), "ALEMS-NET-0005"),
    (ConnectionRefusedError("down"), "ALEMS-NET-0002"),
    (socket.gaierror("dns"), "ALEMS-NET-0001"),
    (ssl.SSLError("tls"), "ALEMS-NET-0003"),
    (_HTTPErr(401), "ALEMS-PROV-0001"),
    (_HTTPErr(429), "ALEMS-PROV-0102"),
    (_HTTPErr(503), "ALEMS-PROV-0201"),
    (ValueError("nothing matches"), GENERIC_CODE),
])
def test_rules(exc, code):
    assert ec.classify(exc) == code


def test_yaml_error_by_module_prefix():
    yaml = pytest.importorskip("yaml")
    try:
        yaml.safe_load("a: [1, 2")
    except yaml.YAMLError as exc:  # real subclass in yaml.parser or yaml.scanner
        assert ec.classify(exc) == "ALEMS-CFG-0002"


def test_component_scoped_rule():
    # 404 maps to PROV-0101 only from core.models; elsewhere nothing matches
    assert ec.classify(_HTTPErr(404), component="core.models.openai") == "ALEMS-PROV-0101"
    assert ec.classify(_HTTPErr(404), component="core.cli") == GENERIC_CODE


def test_exact_status_beats_range():
    rules = [{"http_status_range": [400, 499], "code": "A"}, {"http_status": 429, "code": "B"}]
    facts = {"exc": _HTTPErr(429), "status": 429, "origin": None, "component": None, "message": ""}
    keys = [(ec._match(r, facts), r["code"]) for r in rules]
    assert max(keys)[1] == "B"


def test_more_predicates_win_and_status_precedes_class():
    facts = {"exc": TimeoutError("t"), "status": 504, "origin": None, "component": None, "message": ""}
    both = ec._match({"http_status": 504, "class": "TimeoutError", "code": "X"}, facts)
    status = ec._match({"http_status": 504, "code": "Y"}, facts)
    klass = ec._match({"class": "TimeoutError", "code": "Z"}, facts)
    assert both > status > klass


def test_nearer_class_beats_base():
    facts = {"exc": ConnectionRefusedError(), "status": None, "origin": None, "component": None, "message": ""}
    near = ec._match({"class": "ConnectionRefusedError", "code": "N"}, facts)
    base = ec._match({"class": "OSError", "code": "B"}, facts)
    assert near > base


def test_origin_predicate_uses_innermost_frame():
    def boom():
        raise KeyError("k")
    try:
        boom()
    except KeyError as exc:
        assert ec.origin_module(exc) == __name__


def test_validator_signature_detects_duplicates():
    a = {"class": "X", "http_status": 1, "code": "A"}
    b = {"http_status": 1, "class": "X", "code": "B"}
    assert ec.rule_signature(a) == ec.rule_signature(b)
