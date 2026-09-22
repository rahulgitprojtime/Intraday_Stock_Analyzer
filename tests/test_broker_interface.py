import pytest

from src.broker.base import BrokerAdapter
from src.broker.groww import GrowwAdapter


def test_broker_adapter_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BrokerAdapter()  # abstract — every method must be implemented by a subclass


def test_groww_adapter_satisfies_interface():
    adapter = GrowwAdapter()
    assert isinstance(adapter, BrokerAdapter)
    assert adapter.is_authenticated is False


def test_groww_adapter_rejects_unknown_auth_mode(monkeypatch):
    monkeypatch.setenv("GROWW_AUTH_MODE", "not_a_real_mode")
    with pytest.raises(ValueError):
        GrowwAdapter()


def test_groww_adapter_methods_are_stubbed_not_faked():
    """Every unimplemented method must raise NotImplementedError rather
    than silently returning fabricated data (project hard rule)."""
    adapter = GrowwAdapter()
    with pytest.raises(NotImplementedError):
        adapter.authenticate()
