# 🏎️ F1 Lap Speed Map

A Streamlit app that animates a Formula 1 car's path around a single lap, shaded by speed, using free data from the [OpenF1 API](https://openf1.org).

Pick a date range, a session, a driver and a lap. The app draws the car moving around the circuit, with the line behind it colored by speed and the current speed shown in the corner.

## Features

- **Session search by date:** finds every F1 session (practice, qualifying, sprint, race) in a date range, filterable by session type.
- **Driver picker:** lists only the drivers who took part in the selected session, with number, name and team.
- **Lap picker:** shows every lap with its lap time and marks pit-out laps.
- **Animated speed map:** Play/Pause buttons and a slider to scrub through the lap; a faint track outline shows the full circuit from the start.
- **Live readout:** the current speed (km/h) and elapsed time are shown as text, so you can read speed exactly without matching colors.
- **Lap summary:** lap time, top speed, slowest point and number of samples.
- **Adjustable playback:** 1× (real time) to 16×, and a choice of color scales (YlOrRd, Cividis, Viridis, Plasma).

## How it works

1. `/sessions` gets the sessions in the chosen date range.
2. `/drivers` lists the drivers in the selected session.
3. `/laps` gets the driver's laps; each lap's end time is its start time plus lap duration (or the next lap's start, if no duration is recorded).
4. `/location` (x, y position) and `/car_data` (speed) are requested for that lap's time window.
5. Position and speed are sampled at slightly different timestamps, so speed is **interpolated in time** onto each position timestamp before plotting.
6. Plotly builds an animation that runs in the browser.

API responses are cached for an hour, so switching between selections you've already viewed doesn't call the API again.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501).

## Deploy to Streamlit Community Cloud

1. Push `app.py`, `requirements.txt` and this README to a GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io) and click **Create app**.
3. Select the repo and branch, set the main file to `app.py`, and click **Deploy**.

No API key or secrets are needed.

## Notes and limitations

- OpenF1 has a rate limit. If you see a rate-limit message, wait a few seconds and try again.
- Data for very recent or in-progress sessions may be incomplete.
- The last lap of a session is skipped if OpenF1 has no lap duration for it, because there's no end time to request data up to.
- Position coordinates come straight from OpenF1 and have no units shown; the track shape is correct but not drawn to a real-world scale.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app |
| `requirements.txt` | Python dependencies |

## Credits

Data: [OpenF1](https://openf1.org), an open-source API for Formula 1 timing and telemetry data. This project is unofficial and not associated with Formula 1 or the FIA.
