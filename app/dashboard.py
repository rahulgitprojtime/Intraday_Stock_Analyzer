"""Streamlit dashboard — M6 (spec §19-21).

Reads only data/processed/state.json (written by the worker). Shows the
highest-ranked intraday candidates according to the configured
methodology and why. No order controls, no price levels.

    streamlit run app/dashboard.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.view_model import (  # noqa: E402
    DEFAULT_CATEGORIES,
    RANKABLE_CATEGORIES,
    avoided,
    banners,
    checklist_lines,
    news_links,
    load_state,
    select,
    table_rows,
    universe_caption,
)

STATE_PATH = Path(os.environ.get("INTRADAY_STATE", ROOT / "data" / "processed" / "state.json"))

st.set_page_config(page_title="Intraday Candidates", layout="wide")
st.title("Top intraday candidates")

with st.sidebar:
    mode = st.radio("Mode", ["SCALP", "DAY"],
                    format_func=lambda m: "Scalp (1-min)" if m == "SCALP" else "Day (5-min)")
    top_n = st.selectbox("Top N", [5, 10, 20], index=1)
    categories = st.multiselect("Categories", RANKABLE_CATEGORIES,
                                default=list(DEFAULT_CATEGORIES))
    min_score = st.slider("Min score", 0, 100, 0)
    show_avoid = st.checkbox("Show AVOID (not ranked, for transparency)", value=False)


def _card(r: dict) -> None:
    with st.expander(f"#{r['rank']}  {r['symbol']} · {r['category']} · score {r['score']:.1f}"):
        st.write(f"Profile: {r['profile'].replace('_', ' ').lower()}")
        chips = " ".join(f"`{s['name']}: {s['state']}`" for s in r["setup"]["signals"]
                         if s["state"] != "NONE")
        st.markdown(chips or "No active setups")
        st.markdown("**Prerequisites checked**")
        for line in checklist_lines(r):
            st.write(line)
        links = news_links(r)
        if links:
            st.markdown("**News (real headlines, linked)**")
            for link in links:
                st.markdown(link)
        for c in r["components"]:
            if c["status"] != "available" or c["value"] is None:
                st.caption(f"{c['name']}: unavailable")
                continue
            note = "" if c["weight"] else " (reported, not blended)"
            st.progress(min(1.0, c["value"] / 100), text=f"{c['name']}: {c['value']:.0f}{note}")
        for reason in r["reasons"]:
            st.write(f"- {reason['text']}")
        dq = r["data_quality"]
        missing = f", missing: {', '.join(dq['missing_inputs'])}" if dq["missing_inputs"] else ""
        st.caption(f"Data {dq['status']}, age {dq['data_age_seconds']:.0f} s{missing}")


@st.fragment(run_every=30)
def render() -> None:
    state, error = load_state(STATE_PATH)
    if error:
        st.warning(error)
        return
    for level, text in banners(state, datetime.now()):
        getattr(st, level)(text)
    st.caption(f"As of {state['as_of']} · source: {state['source']} · "
               f"in play: {state['in_play_count']}/{state['universe_count']} · "
               f"feed: {state['feed']['status']}")
    st.caption(universe_caption(state["universe"]))
    vol = {a["symbol"]: a for a in state["universe"].get("active", [])}
    recs = select(state["modes"][mode], categories, min_score, top_n)
    if recs:
        st.dataframe(table_rows(recs, vol), hide_index=True, width="stretch")
        for r in recs:
            _card(r)
    else:
        st.info("No candidates match the filters right now.")
    if show_avoid:
        st.subheader("AVOID - not ranked")
        for r in avoided(state["modes"][mode]):
            st.write(f"{r['symbol']} · raw score {r['score']:.1f} · "
                     f"{', '.join(r['exclusion_reasons'])}")


render()
