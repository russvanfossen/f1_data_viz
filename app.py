"""F1 lap speed map — Streamlit app built on the OpenF1 API (https://openf1.org)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = "https://api.openf1.org/v1"

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


# ---------------------------------------------------------------- figure
def densify(a: np.ndarray, k: int) -> np.ndarray:
    """Insert k-1 linearly interpolated points between each pair, so the trail looks solid."""
    idx = np.arange(len(a))
    fine = np.linspace(0, len(a) - 1, (len(a) - 1) * k + 1)
    return np.interp(fine, idx, a)


def build_figure(df: pd.DataFrame, title: str, speedup: float, colorscale: str) -> go.Figure:
    xs, ys, sp = (df[c].to_numpy(dtype=float) for c in ("x", "y", "speed"))
    t = (df.index - df.index[0]).total_seconds().to_numpy()

    k = 3
    dx, dy, ds = densify(xs, k).round(1), densify(ys, k).round(1), densify(sp, k).round(0)

    frame_ms = max(np.diff(t).mean() * 1000 / speedup, 20)
    cmin, cmax = float(sp.min()), float(sp.max())
    pad = 200

    def trail(n):
        return go.Scatter(
            x=dx[:n], y=dy[:n], mode="markers", hoverinfo="skip",
            marker=dict(size=7, color=ds[:n], colorscale=colorscale, cmin=cmin, cmax=cmax,
                        colorbar=dict(title="km/h", thickness=14, len=0.7)),
        )

    def car(i):
        return go.Scatter(
            x=[xs[i]], y=[ys[i]], mode="markers", hoverinfo="skip",
            marker=dict(size=16, color="white", line=dict(color="black", width=3)),
        )

    def label(i):  # current speed as text, readable without matching colors
        return go.Scatter(
            x=[xs.min() - pad], y=[ys.max() + pad], mode="text", hoverinfo="skip",
            text=[f"<b>{sp[i]:.0f} km/h</b><br>{t[i]:.1f} s"], textposition="bottom right",
            textfont=dict(size=20, family="monospace"),
        )

    track = go.Scatter(x=xs, y=ys, mode="lines", hoverinfo="skip",
                       line=dict(color="#c8c8c8", width=12))

    frames = [
        go.Frame(data=[trail(i * k + 1), car(i), label(i)], traces=[1, 2, 3], name=str(i))
        for i in range(len(xs))
    ]

    play = dict(frame=dict(duration=frame_ms, redraw=False), transition=dict(duration=0),
                fromcurrent=True, mode="immediate")
    pause = dict(frame=dict(duration=0, redraw=False), transition=dict(duration=0), mode="immediate")

    fig = go.Figure(data=[track, trail(1), car(0), label(0)], frames=frames)
    fig.update_layout(
        title=title, height=720, showlegend=False,
        margin=dict(l=10, r=10, t=60, b=10),
        plot_bgcolor="white",
        xaxis=dict(visible=False, range=[xs.min() - pad, xs.max() + pad]),
        yaxis=dict(visible=False, range=[ys.min() - pad, ys.max() + pad],
                   scaleanchor="x", scaleratio=1),  # equal aspect so the track isn't stretched
        updatemenus=[dict(
            type="buttons", direction="left", x=0.01, y=0, xanchor="left", yanchor="top",
            pad=dict(t=10),
            buttons=[dict(label="▶ Play", method="animate", args=[None, play]),
                     dict(label="❚❚ Pause", method="animate", args=[[None], pause])],
        )],
        sliders=[dict(
            x=0.18, len=0.8, y=0, yanchor="top", pad=dict(t=10),
            currentvalue=dict(visible=False),
            steps=[dict(method="animate", label="",
                        args=[[str(i)], dict(frame=dict(duration=0, redraw=False),
                                             transition=dict(duration=0), mode="immediate")])
                   for i in range(len(xs))],
        )],
    )
    return fig


def fmt_laptime(seconds) -> str:
    if pd.isna(seconds):
        return "—"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


# ---------------------------------------------------------------- UI
st.title("🏎️ F1 Lap Speed Map")
st.caption("Data from the [OpenF1 API](https://openf1.org). Pick a session, driver and lap in the sidebar.")

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

    st.header("3. Driver")
    drivers = get("drivers", f"session_key={session_key}")
    if drivers.empty:
        st.warning("No driver list for this session yet.")
        st.stop()
    drivers = drivers.drop_duplicates("driver_number").sort_values("driver_number")
    dlabels = {
        int(r.driver_number): f"#{int(r.driver_number)} {r.full_name} ({r.team_name})"
        for r in drivers.itertuples()
    }
    driver_number = st.selectbox("Driver", list(dlabels), format_func=dlabels.get)

    st.header("4. Lap")
    with st.spinner("Loading laps…"):
        laps = get_laps(session_key, driver_number)
    if laps.empty:
        st.warning("No lap data for this driver in this session.")
        st.stop()
    lap_rows = laps.set_index("lap_number")
    lap_number = st.selectbox(
        "Lap", list(lap_rows.index),
        format_func=lambda n: f"Lap {n} — {fmt_laptime(lap_rows.loc[n, 'lap_duration'])}"
        + (" (pit out)" if lap_rows.loc[n].get("is_pit_out_lap") == True else ""),  # noqa: E712
    )

    st.header("5. Playback")
    speedup = st.select_slider("Playback speed", options=[1, 2, 4, 8, 16], value=4,
                               format_func=lambda v: f"{v}×")
    colorscale = st.selectbox("Color scale", ["YlOrRd", "Cividis", "Viridis", "Plasma"])

lap = lap_rows.loc[lap_number]
with st.spinner("Loading position and speed data…"):
    trace = get_lap_trace(session_key, driver_number, iso(lap["date_start"]), iso(lap["date_end"]))

if len(trace) < 2:
    st.warning("No position/speed data for this lap.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lap time", fmt_laptime(lap["lap_duration"]))
c2.metric("Top speed", f"{trace['speed'].max():.0f} km/h")
c3.metric("Slowest point", f"{trace['speed'].min():.0f} km/h")
c4.metric("Samples", len(trace))

title = f"{session['circuit_short_name']} {session['session_name']} — {dlabels[driver_number]}, lap {lap_number}"
st.plotly_chart(build_figure(trace, title, speedup, colorscale), width="stretch")
