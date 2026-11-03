"""F1 lap speed map — Streamlit app built on the OpenF1 API (https://openf1.org)."""

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

API = "https://api.openf1.org/v1"
PLAYER = (Path(__file__).parent / "player.html").read_text()
MAX_DRIVERS = 4

st.set_page_config(page_title="F1 Lap Speed Map", page_icon="🏎️", layout="wide")


# ---------------------------------------------------------------- API helpers
@st.cache_data(ttl=3600, show_spinner=False)
def get(endpoint: str, query: str) -> pd.DataFrame:
    """GET an OpenF1 endpoint and return it as a DataFrame (empty if no results)."""
    r = requests.get(f"{API}/{endpoint}?{query}", timeout=30)
    if r.status_code == 404:  # OpenF1 answers 404 when a filter matches nothing
        return pd.DataFrame()
    if r.status_code == 429:
        st.error("OpenF1 rate limit hit — wait a few seconds and try again.")
        st.stop()
    r.raise_for_status()
    return pd.DataFrame(r.json())


def iso(ts: pd.Timestamp) -> str:
    """Timestamp → the naive UTC format OpenF1 accepts in date filters."""
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f")


@st.cache_data(ttl=3600, show_spinner=False)
def get_laps(session_key: int, driver_number: int) -> pd.DataFrame:
    laps = get("laps", f"session_key={session_key}&driver_number={driver_number}")
    if laps.empty:
        return laps
    laps = (laps.dropna(subset=["date_start"]).drop_duplicates("lap_number")
            .sort_values("lap_number").reset_index(drop=True))
    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)
    laps["date_end"] = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
    # Laps without a recorded duration end where the next lap starts
    laps["date_end"] = laps["date_end"].fillna(laps["date_start"].shift(-1))
    return laps.dropna(subset=["date_end"])


def build_trace(location: pd.DataFrame, car: pd.DataFrame) -> pd.DataFrame:
    """Line up position and speed samples (which have different timestamps).

    Speed is interpolated in time onto each position timestamp.
    """
    loc = location[["date", "x", "y"]]
    spd = car[["date", "speed"]]
    df = pd.concat([loc, spd])
    df["date"] = pd.to_datetime(df["date"], format="ISO8601", utc=True)
    df = df.groupby("date").first().sort_index()  # merges rows that share a timestamp
    df["speed"] = df["speed"].astype(float).interpolate(method="time")
    return df.dropna(subset=["x", "y", "speed"])


@st.cache_data(ttl=3600, show_spinner=False)
def get_lap_trace(session_key: int, driver_number: int, start: str, end: str) -> pd.DataFrame:
    q = f"session_key={session_key}&driver_number={driver_number}&date>={start}&date<={end}"
    location = get("location", q)
    car = get("car_data", q)
    if location.empty or car.empty:
        return pd.DataFrame()
    return build_trace(location, car)


# ---------------------------------------------------------------- player
def fmt_laptime(seconds) -> str:
    if pd.isna(seconds):
        return "—"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


def player_html(entries: list[dict]) -> tuple[str, int]:
    """Bundle every driver's lap into the canvas player. Data is sent once; the browser animates it."""
    drivers = []
    for e in entries:
        tr = e["trace"]
        t = (tr.index - tr.index[0]).total_seconds()
        drivers.append({
            "name": e["name"], "abbr": e["abbr"], "team": e["team"],
            "lap": int(e["lap"]), "lapTime": fmt_laptime(e["lap_duration"]),
            "t": np.round(t.to_numpy(), 3).tolist(),
            "x": tr["x"].round(1).tolist(), "y": tr["y"].round(1).tolist(),
            "s": tr["speed"].round(1).tolist(),
        })
    speeds = pd.concat([e["trace"]["speed"] for e in entries])
    n = len(entries)
    canvas_h = 560 if n == 1 else 400
    rows = 1 if n == 1 else math.ceil(n / 2)
    height = 120 + rows * (canvas_h + 90)  # separate maps
    if n > 1:
        height = max(height, 860)  # room for a big shared map too
    together_h = height - 230  # shared map fills the same space
    data = {"drivers": drivers, "smin": float(speeds.min()), "smax": float(speeds.max()),
            "canvasH": canvas_h, "togetherH": together_h}
    return PLAYER.replace("__DATA__", json.dumps(data)), height


# ---------------------------------------------------------------- UI
st.title("🏎️ F1 Lap Speed Map")
st.caption("Data from the [OpenF1 API](https://openf1.org). Pick a session, drivers and lap in the sidebar.")

with st.sidebar:
    st.header("1. Timeline")
    today = date.today()
    picked = st.date_input("Date range", value=(today - timedelta(days=30), today))
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Pick an end date.")
        st.stop()
    d_start, d_end = picked

    with st.spinner("Loading sessions…"):
        # +1 day so sessions on the end date are included
        sessions = get("sessions", f"date_start>={d_start}&date_end<={d_end + timedelta(days=1)}")
    if sessions.empty:
        st.warning("No sessions in that date range.")
        st.stop()

    sessions["date_start"] = pd.to_datetime(sessions["date_start"], format="ISO8601", utc=True)
    sessions = sessions.sort_values("date_start", ascending=False)

    types = sorted(sessions["session_type"].dropna().unique())
    default_types = ["Race"] if "Race" in types else types
    chosen_types = st.multiselect("Session types", types, default=default_types)
    sessions = sessions[sessions["session_type"].isin(chosen_types)]
    if sessions.empty:
        st.warning("No sessions of those types.")
        st.stop()

    st.header("2. Session")
    labels = {
        r.session_key: f"{r.date_start:%b %d} · {r.circuit_short_name} ({r.country_name}) — {r.session_name}"
        for r in sessions.itertuples()
    }
    session_key = st.selectbox("Session", list(labels), format_func=labels.get)
    session = sessions.set_index("session_key").loc[session_key]

    st.header("3. Drivers")
    drivers = get("drivers", f"session_key={session_key}")
    if drivers.empty:
        st.warning("No driver list for this session yet.")
        st.stop()
    drivers = drivers.drop_duplicates("driver_number").sort_values("driver_number")
    drivers["driver_number"] = drivers["driver_number"].astype(int)
    info = drivers.set_index("driver_number")
    dlabels = {n: f"#{n} {r.full_name} ({r.team_name})" for n, r in info.iterrows()}
    chosen = st.multiselect(f"Drivers (up to {MAX_DRIVERS})", list(dlabels), default=list(dlabels)[:1],
                            format_func=dlabels.get, max_selections=MAX_DRIVERS)
    if not chosen:
        st.info("Pick at least one driver.")
        st.stop()

    st.header("4. Lap")
    with st.spinner("Loading laps…"):
        all_laps = {n: get_laps(session_key, n) for n in chosen}
    missing = [n for n, l in all_laps.items() if l.empty]
    if missing:
        st.warning("No lap data for: " + ", ".join(f"#{n}" for n in missing))
    all_laps = {n: l for n, l in all_laps.items() if not l.empty}
    if not all_laps:
        st.stop()

    mode = st.radio("Compare", ["Same lap number", "Each driver's fastest lap"])
    if mode == "Same lap number":
        common = sorted(set.intersection(*(set(l["lap_number"]) for l in all_laps.values())))
        if not common:
            st.warning("These drivers have no lap numbers in common.")
            st.stop()
        lap_number = st.selectbox("Lap", common, format_func=lambda n: f"Lap {n}")
        picks = {n: l.set_index("lap_number").loc[lap_number] for n, l in all_laps.items()}
    else:
        picks = {}
        for n, l in all_laps.items():
            timed = l.dropna(subset=["lap_duration"])
            if not timed.empty:
                row = timed.loc[timed["lap_duration"].idxmin()]
                picks[n] = row.rename(row["lap_number"])
        if not picks:
            st.warning("No timed laps for these drivers.")
            st.stop()

# Fetch each driver's lap (cached per driver + lap, so going back to one you've seen is instant)
entries = []
with st.spinner("Loading position and speed data…"):
    for n, lap in picks.items():
        lap_no = lap["lap_number"] if "lap_number" in lap else lap.name
        trace = get_lap_trace(session_key, n, iso(lap["date_start"]), iso(lap["date_end"]))
        if len(trace) < 2:
            st.warning(f"No position/speed data for #{n}, lap {lap_no}.")
            continue
        r = info.loc[n]
        entries.append({"name": f"#{n} {r.full_name}", "abbr": r["name_acronym"] if isinstance(r.get("name_acronym"), str) else str(n),
                        "team": r.team_name, "lap": lap_no, "lap_duration": lap["lap_duration"],
                        "trace": trace})

if not entries:
    st.stop()

st.subheader(f"{session['circuit_short_name']} — {session['session_name']}")
st.dataframe(
    pd.DataFrame([{
        "Driver": e["name"], "Lap": int(e["lap"]), "Lap time": fmt_laptime(e["lap_duration"]),
        "Top speed (km/h)": round(e["trace"]["speed"].max()),
        "Slowest (km/h)": round(e["trace"]["speed"].min()),
    } for e in entries]),
    hide_index=True,
)

html, height = player_html(entries)
components.html(html, height=height)
st.caption("All cars run on the same lap clock. **One map** shows every car together with a short "
           "speed-colored tail and the running order; **Separate maps** shows each driver's full speed trace.")
