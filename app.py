"""F1 lap speed map — Streamlit app built on the OpenF1 API (https://openf1.org)."""

import html
import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

API = "https://api.openf1.org/v1"
PLAYER = (Path(__file__).parent / "player.html").read_text()
PLACEHOLDER = (Path(__file__).parent / "placeholder.html").read_text()
MAX_DRIVERS = 4
PLAYER_MIN_H, PLAYER_START_H, PLAYER_MAX_H = 420, 620, 1100
CACHE_TTL = 6 * 3600          # seconds; historical data doesn't change
CHUNK = pd.Timedelta(minutes=20)  # fallback window when a full-session request is too big

st.set_page_config(page_title="F1 Lap Speed Map", page_icon="🏎️", layout="wide")
# Trim Streamlit's large top padding so more of the screen goes to the map
st.markdown("<style>.block-container{padding-top:2.2rem;padding-bottom:1rem}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------- polite API access
class RateLimiter:
    """Keeps every visitor of this app, combined, inside OpenF1's free tier (3/s and 30/min)."""

    def __init__(self, per_sec=3, per_min=30):
        self.per_sec, self.per_min = per_sec, per_min
        self.calls = deque()
        self.lock = threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
                last_sec = sum(1 for t in self.calls if now - t < 1)
                if len(self.calls) < self.per_min and last_sec < self.per_sec:
                    self.calls.append(now)
                    return
            time.sleep(0.25)


@st.cache_resource
def limiter() -> RateLimiter:
    return RateLimiter()


class TooMuchData(Exception):
    """OpenF1 refused or timed out on a request; retry it in smaller time windows."""


def fetch(endpoint: str, query: str) -> list:
    """GET one OpenF1 endpoint. Waits for the rate limit and retries if OpenF1 says slow down."""
    for attempt in range(4):
        limiter().wait()
        try:
            r = requests.get(f"{API}/{endpoint}?{query}", timeout=90)
        except requests.Timeout:
            raise TooMuchData
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2 * (attempt + 1))))
            continue
        if r.status_code == 404:  # OpenF1 answers 404 when a filter matches nothing
            return []
        if r.status_code in (413, 422) or r.status_code >= 500:
            raise TooMuchData
        r.raise_for_status()
        return r.json()
    raise requests.HTTPError("OpenF1 is rate limiting requests — try again in a minute.")


def iso(ts: pd.Timestamp) -> str:
    """Timestamp → the naive UTC format OpenF1 accepts in date filters."""
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f")


def fetch_range(endpoint: str, session_key: int, driver_number: int,
                start: pd.Timestamp, end: pd.Timestamp) -> list:
    """One driver's samples between two times; split into smaller windows if OpenF1 refuses."""
    base = f"session_key={session_key}&driver_number={driver_number}"
    try:
        return fetch(endpoint, f"{base}&date>={iso(start)}&date<={iso(end)}")
    except TooMuchData:
        rows, t = [], start
        while t < end:
            t2 = min(t + CHUNK, end)
            rows += fetch(endpoint, f"{base}&date>={iso(t)}&date<={iso(t2)}")
            t = t2
        return rows


# ---------------------------------------------------------------- cached data
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_sessions(d_start: date, d_end: date) -> pd.DataFrame:
    # +1 day so sessions on the end date are included
    return pd.DataFrame(fetch("sessions", f"date_start>={d_start}&date_end<={d_end + timedelta(days=1)}"))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_drivers(session_key: int) -> pd.DataFrame:
    df = pd.DataFrame(fetch("drivers", f"session_key={session_key}"))
    if df.empty:
        return df
    df = df.drop_duplicates("driver_number").sort_values("driver_number")
    df["driver_number"] = df["driver_number"].astype(int)
    return df.set_index("driver_number")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_session_laps(session_key: int) -> pd.DataFrame:
    """Every lap of every driver in one request."""
    laps = pd.DataFrame(fetch("laps", f"session_key={session_key}"))
    if laps.empty:
        return laps
    laps = (laps.dropna(subset=["date_start"]).drop_duplicates(["driver_number", "lap_number"])
            .sort_values(["driver_number", "lap_number"]).reset_index(drop=True))
    laps["driver_number"] = laps["driver_number"].astype(int)
    laps["date_start"] = pd.to_datetime(laps["date_start"], format="ISO8601", utc=True)
    laps["date_end"] = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
    # Laps without a recorded duration end where that driver's next lap starts
    laps["date_end"] = laps["date_end"].fillna(laps.groupby("driver_number")["date_start"].shift(-1))
    return laps.dropna(subset=["date_end"]).reset_index(drop=True)


def driver_window(laps: pd.DataFrame, driver_number: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    dl = laps[laps["driver_number"] == driver_number]
    pad = pd.Timedelta(seconds=5)
    return dl["date_start"].min() - pad, dl["date_end"].max() + pad


@st.cache_data(ttl=CACHE_TTL, show_spinner=False, max_entries=120)
def get_positions(session_key: int, driver_number: int, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.DataFrame(fetch_range("location", session_key, driver_number, start, end))
    return df[["date", "x", "y"]] if not df.empty else df


@st.cache_data(ttl=CACHE_TTL, show_spinner=False, max_entries=120)
def get_speeds(session_key: int, driver_number: int, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.DataFrame(fetch_range("car_data", session_key, driver_number, start, end))
    return df[["date", "speed"]] if not df.empty else df


def build_trace(location: pd.DataFrame, car: pd.DataFrame) -> pd.DataFrame:
    """Line up position and speed samples (which have different timestamps).

    Speed is interpolated in time onto each position timestamp.
    """
    df = pd.concat([location[["date", "x", "y"]], car[["date", "speed"]]])
    df["date"] = pd.to_datetime(df["date"], format="ISO8601", utc=True)
    df = df.groupby("date").first().sort_index()  # merges rows that share a timestamp
    df["speed"] = df["speed"].astype(float).interpolate(method="time")
    return df.dropna(subset=["x", "y", "speed"]).astype("float32")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False, max_entries=120)
def get_driver_trace(session_key: int, driver_number: int, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """A driver's whole session, positions + speed lined up. Laps are sliced from this in memory."""
    loc = get_positions(session_key, driver_number, start, end)
    car = get_speeds(session_key, driver_number, start, end)
    if loc.empty or car.empty:
        return pd.DataFrame()
    return build_trace(loc, car)


# ---------------------------------------------------------------- loading screen
LOADER_CSS = """
<style>
.f1-load { border:1px solid rgba(128,128,128,.35); border-radius:12px; padding:22px 24px;
           background:rgba(128,128,128,.07); margin:8px 0 18px; }
.f1-load h4 { margin:0 0 2px; font-size:1.25rem; }
.f1-load .sub { opacity:.75; font-size:.95rem; margin-bottom:14px; }
.f1-track { position:relative; height:44px; overflow:hidden; border-radius:6px;
            background:repeating-linear-gradient(90deg, rgba(128,128,128,.45) 0 18px, transparent 18px 36px)
                       center/100% 3px no-repeat; }
.f1-car { position:absolute; top:4px; font-size:30px; line-height:1; animation:f1-drive 1.8s linear infinite; }
.f1-car span { display:inline-block; transform:scaleX(-1); }   /* face the direction of travel */
.f1-flag { position:absolute; right:4px; top:6px; font-size:26px; }
@keyframes f1-drive { from { left:-48px; } to { left:100%; } }
.f1-bar { height:8px; border-radius:4px; background:rgba(128,128,128,.25); margin:12px 0 4px; overflow:hidden; }
.f1-bar div { height:100%; background:currentColor; opacity:.8; }
.f1-steps { list-style:none; padding:0; margin:12px 0 0; font-size:1rem; }
.f1-steps li { margin:4px 0; }
.f1-steps .mark { display:inline-block; width:1.6em; font-weight:700; }
.f1-steps .pending { opacity:.55; }
.f1-steps .active { font-weight:600; }
@media (prefers-reduced-motion: reduce) { .f1-car { animation:none; left:40%; } }
</style>
"""
MARKS = {"done": "✓", "active": "▶", "pending": "○"}


def loader_html(title, sub="", steps=None, progress=None) -> str:
    bar = f'<div class="f1-bar"><div style="width:{progress * 100:.0f}%"></div></div>' if progress is not None else ""
    items = "".join(
        f'<li class="{state}"><span class="mark">{MARKS[state]}</span>{html.escape(label)}</li>'
        for label, state in (steps or [])
    )
    return (LOADER_CSS + f'<div class="f1-load" role="status" aria-live="polite">'
            f'<h4>{html.escape(title)}</h4><div class="sub">{html.escape(sub)}</div>'
            f'<div class="f1-track"><div class="f1-car"><span>🏎️</span></div><div class="f1-flag">🏁</div></div>'
            f'{bar}<ul class="f1-steps">{items}</ul></div>')


class Loader:
    """Big friendly loading card in the main area. Only appears for data this visitor hasn't seen yet."""

    def __init__(self, slot):
        self.slot = slot

    def show(self, title, sub="", steps=None, progress=None):
        self.slot.markdown(loader_html(title, sub, steps, progress), unsafe_allow_html=True)

    def clear(self):
        self.slot.empty()


STEPS = ["Pick a date range", "Choose a session", "Choose drivers", "Choose a lap"]
HINTS = [
    "Use the date range in the sidebar to find F1 sessions.",
    "Pick a session from the sidebar. Its lap times load right away.",
    "Add up to 4 drivers to compare. Their position and speed data loads only when you pick them.",
    "Choose how to compare, then a lap, and the animated map appears here.",
]


def show_placeholder(step: int):
    """Fill the map's spot with the circuit graphic and a checklist of what's left, then stop the run."""
    items = "".join(
        f'<li class="{"done" if i < step else "active" if i == step else "pending"}">'
        f'<span class="mark">{MARKS["done" if i < step else "active" if i == step else "pending"]}</span>{label}</li>'
        for i, label in enumerate(STEPS)
    )
    page = (PLACEHOLDER.replace("__TITLE__", "Ready when you are")
            .replace("__NEXT__", HINTS[step]).replace("__STEPS__", items))
    with loader.slot.container():
        components.html(page, height=420)
    st.stop()


def seen() -> set:
    return st.session_state.setdefault("seen", set())


@contextmanager
def api_errors():
    try:
        yield
    except (requests.RequestException, TooMuchData) as e:
        loader.clear()
        st.error(f"Couldn't get data from OpenF1: {str(e) or 'the request was too large or timed out'}. "
                 "Wait a moment and try again.")
        st.stop()


def load_session(session_key: int):
    key = ("session", session_key)
    if key not in seen():
        loader.show("Loading session", "Getting the driver list and every lap time, once for the whole session.",
                    [("Driver list", "active"), ("Lap times for every driver", "pending")])
    drivers = get_drivers(session_key)
    if key not in seen():
        loader.show("Loading session", "Getting the driver list and every lap time, once for the whole session.",
                    [("Driver list", "done"), ("Lap times for every driver", "active")])
    laps = get_session_laps(session_key)
    seen().add(key)
    loader.clear()
    return drivers, laps


def load_driver(session_key: int, n: int, label: str, laps: pd.DataFrame,
                count: tuple[int, int] | None = None) -> pd.DataFrame:
    """Fetch one driver's whole session (cached). Every lap after that is instant."""
    key = ("driver", session_key, n)
    start, end = driver_window(laps, n)
    if key in seen():
        return get_driver_trace(session_key, n, start, end)

    title = f"Loading {label}" + (f"  ·  {count[0]} of {count[1]}" if count else "")
    sub = "Downloading this driver's whole session once, so switching laps is instant. Long races can take ~30 s."
    progress = (count[0] - 1) / count[1] if count else None
    loader.show(title, sub, [("Car positions", "active"), ("Speed", "pending"), ("Lining up the data", "pending")], progress)
    get_positions(session_key, n, start, end)
    loader.show(title, sub, [("Car positions", "done"), ("Speed", "active"), ("Lining up the data", "pending")], progress)
    get_speeds(session_key, n, start, end)
    loader.show(title, sub, [("Car positions", "done"), ("Speed", "done"), ("Lining up the data", "active")], progress)
    trace = get_driver_trace(session_key, n, start, end)
    seen().add(key)
    return trace


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
            "x": tr["x"].astype(float).round(1).tolist(), "y": tr["y"].astype(float).round(1).tolist(),
            "s": tr["speed"].astype(float).round(1).tolist(),
        })
    speeds = pd.concat([e["trace"]["speed"] for e in entries])
    # The player resizes itself to the visible window, within these limits (px)
    data = {"drivers": drivers, "smin": float(speeds.min()), "smax": float(speeds.max()),
            "minH": PLAYER_MIN_H, "maxH": PLAYER_MAX_H}
    height = PLAYER_START_H
    return PLAYER.replace("__DATA__", json.dumps(data)), height


# ---------------------------------------------------------------- UI
st.title("🏎️ F1 Lap Speed Map")
st.caption("Data from the [OpenF1 API](https://openf1.org). Pick a session, drivers and lap in the sidebar.")
st.caption("View the repo [here](https://github.com/russvanfossen/f1_data_viz).")
loader = Loader(st.empty())  # loading card sits at the top of the main area
if "toast" in st.session_state:
    st.toast(st.session_state.pop("toast"), icon="✅")


def short(n: int) -> str:
    r = info.loc[n]
    return r["name_acronym"] if isinstance(r.get("name_acronym"), str) else f"#{n}"


with st.sidebar:
    st.header("1. Timeline")
    today = date.today()
    picked = st.date_input("Date range", value=(), max_value=today, format="YYYY-MM-DD",
                           help="Pick a start date, then an end date.")
    if not isinstance(picked, tuple) or len(picked) != 2:
        show_placeholder(0)
    d_start, d_end = picked

    with api_errors():
        if ("sessions", d_start, d_end) not in seen():
            loader.show("Finding sessions", f"Looking for F1 sessions between {d_start:%b %d} and {d_end:%b %d}.")
        sessions = get_sessions(d_start, d_end)
        seen().add(("sessions", d_start, d_end))
        loader.clear()
    if sessions.empty:
        st.warning("No sessions in that date range.")
        st.stop()

    sessions["date_start"] = pd.to_datetime(sessions["date_start"], format="ISO8601", utc=True)
    sessions = sessions.sort_values("date_start", ascending=False)

    types = sorted(sessions["session_type"].dropna().unique())
    chosen_types = st.multiselect("Session types", types, default=[], placeholder="All session types")
    if chosen_types:  # empty means show every type
        sessions = sessions[sessions["session_type"].isin(chosen_types)]

    st.header("2. Session")
    labels = {
        r.session_key: f"{r.date_start:%b %d} · {r.circuit_short_name} ({r.country_name}) — {r.session_name}"
        for r in sessions.itertuples()
    }
    session_key = st.selectbox("Session", list(labels), format_func=labels.get, index=None,
                               placeholder="Choose a session")
    if session_key is None:
        show_placeholder(1)
    session_key = int(session_key)
    session = sessions.set_index("session_key").loc[session_key]

    with api_errors():
        info, laps = load_session(session_key)
    if info.empty or laps.empty:
        st.warning("No driver or lap data for this session yet.")
        st.stop()

    st.header("3. Drivers")
    have_laps = set(laps["driver_number"])
    dlabels = {n: f"#{n} {r.full_name} ({r.team_name})" for n, r in info.iterrows() if n in have_laps}
    chosen = st.multiselect(f"Drivers (up to {MAX_DRIVERS})", list(dlabels), default=[],
                            format_func=dlabels.get, max_selections=MAX_DRIVERS,
                            placeholder="Choose drivers to compare")

    not_loaded = [n for n in dlabels if ("driver", session_key, n) not in seen()]
    if not_loaded:
        mins = max(1, round(len(not_loaded) * 2 / 30 + 0.4))  # 2 requests per driver at 30/min
        preload = st.button(f"⚡ Preload all {len(not_loaded)} drivers (~{mins} min)",
                            help="Downloads every driver's session now, so any driver and lap opens instantly.")
    else:
        preload = False
        st.caption("✓ Every driver in this session is loaded.")
    if preload:
        with api_errors():
            for k, n in enumerate(not_loaded, 1):
                load_driver(session_key, n, f"{short(n)} — {info.loc[n, 'full_name']}", laps, (k, len(not_loaded)))
        st.session_state["toast"] = f"All {len(not_loaded)} drivers loaded — every lap opens instantly now."
        st.rerun()
    if not chosen:
        show_placeholder(2)

    st.header("4. Lap")
    mode = st.radio("Compare", ["Same lap number", "Each driver's fastest lap"], index=None)
    if mode is None:
        show_placeholder(3)
    dlaps = {n: laps[laps["driver_number"] == n] for n in chosen}
    if mode == "Same lap number":
        common = sorted(set.intersection(*(set(l["lap_number"]) for l in dlaps.values())))
        if not common:
            st.warning("These drivers have no lap numbers in common.")
            st.stop()
        lap_number = st.selectbox("Lap", common, format_func=lambda n: f"Lap {n}", index=None,
                                  placeholder="Choose a lap")
        if lap_number is None:
            show_placeholder(3)
        picks = {n: l.set_index("lap_number").loc[lap_number] for n, l in dlaps.items()}
    else:
        picks = {}
        for n, l in dlaps.items():
            timed = l.dropna(subset=["lap_duration"])
            if not timed.empty:
                row = timed.loc[timed["lap_duration"].idxmin()]
                picks[n] = row.rename(row["lap_number"])
        if not picks:
            st.warning("No timed laps for these drivers.")
            st.stop()


with api_errors():
    # Each chosen driver's whole session (cached), then the lap is sliced out in memory
    entries = []
    for n, lap in picks.items():
        full = load_driver(session_key, n, f"{short(n)} — {info.loc[n, 'full_name']}", laps)
        lap_no = lap["lap_number"] if "lap_number" in lap else lap.name
        trace = full.loc[lap["date_start"]:lap["date_end"]] if not full.empty else full
        if len(trace) < 2:
            st.warning(f"No position/speed data for #{n}, lap {lap_no}.")
            continue
        r = info.loc[n]
        entries.append({"name": f"#{n} {r.full_name}", "abbr": short(n), "team": r.team_name,
                        "lap": lap_no, "lap_duration": lap["lap_duration"], "trace": trace})
    loader.clear()

if not entries:
    st.stop()

st.markdown(f"**{session['circuit_short_name']} — {session['session_name']}** · "
            "all cars run on the same lap clock, each with a short speed-colored tail.")
page, height = player_html(entries)
components.html(page, height=height)

st.dataframe(
    pd.DataFrame([{
        "Driver": e["name"], "Lap": int(e["lap"]), "Lap time": fmt_laptime(e["lap_duration"]),
        "Top speed (km/h)": round(float(e["trace"]["speed"].max())),
        "Slowest (km/h)": round(float(e["trace"]["speed"].min())),
    } for e in entries]),
    hide_index=True,
)
