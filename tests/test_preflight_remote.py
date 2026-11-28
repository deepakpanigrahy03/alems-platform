"""Preflight stops a *_remote provider whose address is this machine, with one message."""
import pytest

from core.utils import preflight as pf


def test_remote_pointing_here_stops_with_one_message():
    with pytest.raises(SystemExit) as exc:
        pf._check_remote_is_remote("vllm_remote", {"base_url": "http://127.0.0.1:8000/v1"})
    assert "remote by name" in str(exc.value)


def test_other_providers_and_missing_url_pass():
    pf._check_remote_is_remote("vllm_local", {"base_url": "http://127.0.0.1:8000/v1"})
    pf._check_remote_is_remote("ollama_remote", {})
