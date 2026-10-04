"""39.5.2a: global logging options in core/cli/main.py."""

from core.cli.main import _pop_logging_options


def test_leading_options_consumed():
    rest, layer = _pop_logging_options(["--quiet", "--log-level", "INFO", "sandbox", "info"])
    assert rest == ["sandbox", "info"]
    assert layer == {"mode": "quiet", "level": "INFO"}


def test_subcommand_flags_untouched():
    rest, layer = _pop_logging_options(["sandbox", "--verbose"])
    assert rest == ["sandbox", "--verbose"] and layer == {}


def test_help_left_for_dispatch():
    rest, layer = _pop_logging_options(["--help"])
    assert rest == ["--help"] and layer == {}
