# tests/test_conformance.py
# 39.5.1a.3: conformance is derived from the SDK contracts and gates loading.
# Replaces the 39.3 tests, which asserted rules never checked against real
# plugins (G44). Run: venv/bin/python -m pytest tests/test_conformance.py -v
import textwrap

import pytest

from alems_sdk.conformance import run_conformance
from alems_sdk.config_schema import NO_SETTINGS
from alems_sdk.readers import BaseReader


def _meta(group, **over):
    """A complete built manifest for group."""
    m = {"plugin_id": "p", "extension_point": group, "family": "measurement",
         "version": "0.1.0", "sdk_range": ">=1.0,<2.0", "description": "test plugin"}
    m.update(over)
    return m


def _reader(fidelity="MEASURED", bound=None):
    """A concrete reader class with every abstract method implemented."""
    attrs = {"FIDELITY": fidelity, "ERROR_BOUND": bound}
    for name in getattr(BaseReader, "__abstractmethods__", ()):
        attrs[name] = lambda self, *a, **k: None
    return type("FakeReader", (BaseReader,), attrs)


def _passed(cls, group="alems.readers.energy", origin="external", **over):
    return run_conformance("p", _meta(group, **over), cls=cls, origin=origin)


def test_good_reader_passes():
    assert _passed(_reader()).passed


@pytest.mark.parametrize("value", ["MEASURED", "INFERRED", "LIMITED", "SYNTHETIC"])
def test_fidelity_vocabulary(value):
    cls = _reader(value, bound="±10%" if value == "INFERRED" else None)
    assert _passed(cls).passed


def test_missing_fidelity_fails():
    assert not _passed(_reader(fidelity=None)).passed


def test_inferred_without_error_bound_fails():
    assert not _passed(_reader("INFERRED", bound=None)).passed


def test_contract_violation_fails():
    class NotAReader:
        FIDELITY = "MEASURED"
    assert not _passed(NotAReader).passed


def test_abstract_method_left_fails():
    class Half(BaseReader):
        FIDELITY = "MEASURED"
    if not getattr(Half, "__abstractmethods__", None):
        pytest.skip("BaseReader has no abstract methods")
    assert not _passed(Half).passed


def test_default_config_schema_is_no_settings():
    assert _reader().get_config_schema() == NO_SETTINGS


def test_flat_config_schema_fails():
    cls = _reader()
    cls.get_config_schema = classmethod(lambda c: {"k": {"type": "int"}})
    assert not _passed(cls).passed


def test_instance_method_config_schema_fails():
    cls = _reader()
    cls.get_config_schema = lambda self: dict(NO_SETTINGS)
    assert not _passed(cls).passed


def test_incomplete_manifest_fails():
    report = run_conformance("p", _meta("alems.readers.energy", description=""),
                             cls=_reader(), origin="external")
    assert not report.passed


def test_unknown_group_fails():
    assert not _passed(_reader(), group="alems.nope").passed


def _module_class(tmp_path, monkeypatch, source, modname):
    """Write source as an importable module and return its Plugin class."""
    path = tmp_path / ("%s.py" % modname)
    path.write_text(textwrap.dedent(source))
    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib
    return importlib.import_module(modname).Plugin


@pytest.mark.parametrize("line", ["import core", "import core.readers",
                                  "from core import x", "from core.readers import y",
                                  "import scripts"])
def test_external_core_import_fails(tmp_path, monkeypatch, line):
    src = """
    try:
        %s
    except Exception:
        pass
    from alems_sdk.readers import BaseReader
    class Plugin(BaseReader):
        FIDELITY = "MEASURED"
    for _n in list(getattr(Plugin, "__abstractmethods__", ())):
        setattr(Plugin, _n, lambda self, *a, **k: None)
    Plugin.__abstractmethods__ = frozenset()
    """ % line
    modname = "conf_mod_%d" % abs(hash(line))
    cls = _module_class(tmp_path, monkeypatch, src, modname)
    assert not _passed(cls, origin="external").passed


def test_runtime_component_may_import_core(tmp_path, monkeypatch):
    src = """
    try:
        import core
    except Exception:
        pass
    from alems_sdk.readers import BaseReader
    class Plugin(BaseReader):
        FIDELITY = "MEASURED"
    for _n in list(getattr(Plugin, "__abstractmethods__", ())):
        setattr(Plugin, _n, lambda self, *a, **k: None)
    Plugin.__abstractmethods__ = frozenset()
    """
    cls = _module_class(tmp_path, monkeypatch, src, "conf_mod_runtime")
    assert _passed(cls, origin="runtime").passed


def test_callable_group_accepts_function():
    def check(provider_config):
        """Preflight check."""
        return {"ok": True}
    meta = _meta("alems.preflight.checks", family="execution")
    assert run_conformance("p", meta, cls=check, origin="runtime").passed


def test_callable_group_rejects_class():
    meta = _meta("alems.preflight.checks", family="execution")
    assert not run_conformance("p", meta, cls=_reader(), origin="runtime").passed
