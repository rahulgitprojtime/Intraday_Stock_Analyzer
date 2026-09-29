"""Paper trading & backtests page (DECISIONS #29). SIMULATION ONLY.

Reads the SQLite ledgers written by the worker (`--paper`) and by
scripts/backtest.py / paper_replay.py. Display only: no order controls.
Opened from the sidebar of `streamlit run app/dashboard.py`.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.paper_view import DISCLAIMER, live_view, open_ledger, run_options, run_report  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402

RAW = load_yaml("paper.yaml")
PAPER_DB = Path(os.environ.get("PAPER_LEDGER", ROOT / RAW["ledger"]))
BACKTEST_DB = Path(os.environ.get("BACKTEST_LEDGER", ROOT / RAW["backtest_ledger"]))

st.set_page_config(page_title="Paper trading", layout="wide")
st.title("Paper trading & backtests")
st.warning(DISCLAIMER)


def _metrics_table(m: dict) -> None:
    st.dataframe([{"metric": k.replace("_", " "), "value": str(v)} for k, v in m.items()],
                 hide_index=True, width="stretch")


def _pick(runs: list[dict], key: str) -> dict:
    labels = {f"{r['run_id']}  ({r['strategy']}, {r['started_at']})": r for r in runs}
    return labels[st.selectbox("Run", list(labels), key=key)]


live_tab, bt_tab = st.tabs(["Live paper", "Backtests"])

with live_tab:
    @st.fragment(run_every=30)
    def render_live() -> None:
        db = open_ledger(PAPER_DB)
        runs = run_options(db, "PAPER") if db else []
        if not runs:
            st.info("No paper-trading runs yet. Start the worker with `--paper` "
                    "(or `enabled: true` in config/paper.yaml).")
            return
        view = live_view(db, _pick(runs, "live_run"))
        c = st.columns(4)
        c[0].metric("Equity", f"{view['equity']:,.2f}")
        c[1].metric("Realized net P&L", f"{view['realized_net']:,.2f}")
        c[2].metric("Unrealized P&L", f"{view['unrealized']:,.2f}")
        c[3].metric("Charges paid", f"{view['charges']:,.2f}")
        st.caption(f"As of {view['as_of'] or '-'}")
        st.subheader("Open positions")
        if view["positions"]:
            st.dataframe(view["positions"], hide_index=True, width="stretch")
        else:
            st.write("None")
        st.subheader("Working orders")
        st.dataframe(view["open_orders"] or [{"status": "none"}], hide_index=True,
                     width="stretch")
        if view["equity_curve"]:
            st.line_chart(view["equity_curve"], x="at", y="equity")
        st.subheader("Closed trades")
        st.dataframe(view["trades"] or [{"trades": "none yet"}], hide_index=True,
                     width="stretch")
        with st.expander("All fills (with every charge)"):
            st.dataframe(view["fills"] or [{"fills": "none"}], hide_index=True, width="stretch")

    render_live()

with bt_tab:
    db = open_ledger(BACKTEST_DB)
    runs = run_options(db) if db else []
    runs = [r for r in runs if r["mode"] in ("BACKTEST", "REPLAY")]
    if not runs:
        st.info("No backtests yet. Run `python scripts/backtest.py --strategy orb "
                "--from YYYY-MM-DD --to YYYY-MM-DD`.")
    else:
        run = _pick(runs, "bt_run")
        rep = run_report(db, run)
        m = rep["metrics"]
        c = st.columns(5)
        c[0].metric("Net P&L (after costs)", f"{m['net_pnl']:,.2f}")
        c[1].metric("Win rate %", str(m["win_rate"]))
        c[2].metric("Max drawdown", f"{m['max_drawdown']:,.2f}")
        c[3].metric("Sharpe (daily, ann.)", str(m["sharpe"]))
        c[4].metric("Trades", m["trades"])
        if m["sample_warning"]:
            st.caption(m["sample_warning"])
        if rep["equity"]:
            st.subheader("Equity curve")
            st.line_chart(rep["equity"], x="at", y="equity")
        st.subheader("Daily P&L")
        st.dataframe(rep["daily"], hide_index=True, width="stretch")
        st.subheader("Trades")
        st.dataframe(rep["trades"] or [{"trades": "none"}], hide_index=True, width="stretch")
        with st.expander("All metrics and parameters"):
            _metrics_table(m)
            st.json(run["params"])
