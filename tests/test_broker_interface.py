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


def test_simulated_trading_cannot_reach_groww():
    """DECISIONS #20/#29: orders exist only inside the simulated brokers.
    The simulation layer must not import the Groww adapter or SDK."""
    import re
    from pathlib import Path

    files = list(Path("src/paper").rglob("*.py")) + [Path("scripts/paper_replay.py"),
                                                      Path("app/pages/1_Paper_trading.py"),
                                                      Path("src/research/setup_backtest.py"),
                                                      Path("scripts/setup_study.py")]
    for path in files:
        src = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", src, re.M)
        assert not [m for m in imports if m.startswith(("src.broker", "growwapi"))], path


def test_no_code_calls_a_groww_order_endpoint():
    """The Groww SDK has order endpoints (place/modify/cancel order, ...); no
    file in the repo may call anything order-like on a Groww client."""
    import re
    from pathlib import Path

    pattern = re.compile(r"(?:_client|api_client|groww\w*)\s*\.\s*\w*order\w*\s*\(", re.I)
    for path in [*Path("src").rglob("*.py"), *Path("scripts").rglob("*.py"),
                 *Path("app").rglob("*.py")]:
        assert not pattern.findall(path.read_text(encoding="utf-8")), path
    for path in Path("src/broker").rglob("*.py"):       # the only package that talks to Groww
        calls = re.findall(r"\.(\w*order\w*)\s*\(", path.read_text(encoding="utf-8"), re.I)
        assert not calls, (path, calls)


def test_every_order_broker_is_simulated():
    """No live implementation of the order interface exists (DECISIONS #29)."""
    from src.paper.broker import Broker, SimulatedBroker

    def subclasses(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from subclasses(sub)

    impls = list(subclasses(Broker))
    assert impls and all(issubclass(c, SimulatedBroker) for c in impls), impls
