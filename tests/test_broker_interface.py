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


def test_api_client_requires_authentication():
    """The live feed (M7, src/broker/groww_feed.py) needs the SDK client;
    it is only handed out after a successful authenticate()."""
    from src.broker.base import AuthenticationError
    with pytest.raises(AuthenticationError):
        GrowwAdapter().api_client()


def test_broker_interface_exposes_no_order_execution():
    """Recommendation-only product (DECISIONS.md #8): no order methods."""
    banned = ("order", "position", "holding", "portfolio", "margin")
    names = [n.lower() for n in dir(BrokerAdapter)]
    assert not [n for n in names if any(b in n for b in banned)]


def test_real_growwapi_sdk_is_detected_when_installed():
    """Regression: a wrong exception name inside the guarded import made the
    adapter report the installed SDK as missing."""
    pytest.importorskip("growwapi")
    from src.broker import groww

    assert groww.GROWWAPI_INSTALLED is True
