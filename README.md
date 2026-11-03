# 🏎️ F1 Lap Speed Map

A Streamlit app that animates a Formula 1 car's path around a single lap, shaded by speed, using free data from the [OpenF1 API](https://openf1.org).

Every visit starts fresh with nothing selected. Pick a date range, a session, up to four drivers and a lap. The app draws each car moving around the circuit on a shared lap clock, with the line behind it colored by speed.

App was based my F1 viz notebook [GitHub Link](https://github.com/russvanfossen/Data_Science_Portfolio/blob/main/f1data.ipynb). /
The data exploration was done mostly by hand with Claude consulted for ideas on the more complex data manipulations (mostly the reshaping of the numpy arrays). Claude also helped develop the Streamlit app and animations. 

View my portfolio [here](https://github.com/russvanfossen/Data_Science_Portfolio)

## Features

- **Session search by date:** finds every F1 session (practice, qualifying, sprint, race) in a date range, filterable by session type.
- **Compare up to 4 drivers:** pick drivers from the selected session, then compare either the same lap number or each driver's fastest lap.
- **All drivers on one map:** every car runs on the same track with a short speed-colored tail, its abbreviation and a live running order (P1, P2…).
- **Synchronized animation:** all cars run on the same lap clock, so you can see where one driver gains on another.
- **Shapes, not just colors:** each driver has a shape (● ■ ◆ ▲) shown on the map and in the running order.
- **Live readout:** each driver's current speed is shown as text in the running order, so you can read it exactly without matching colors.
- **Lap summary table:** lap, lap time, top speed and slowest point for each driver.
- **Nothing loads until you ask:** every picker starts empty, and an animated placeholder circuit with a checklist shows which step is next. A driver's telemetry downloads only once you pick that driver.
- **Load once, switch instantly:** lap times for the whole session load when you pick it, and each driver's full session loads once, so changing laps doesn't wait on the API. A preload button can fetch every driver up front.
- **Fits your screen:** the map resizes to fill the browser window below the controls, so the whole track is visible without scrolling.
- **Player controls:** Play/Pause, Restart, a slider to scrub through the lap and 1×–8× playback speed, all without reloading the page.
- **Color scale:** yellow = slowest, red = fastest (YlOrRd).

## How it works

1. `/sessions` gets the sessions in the chosen date range.
2. Leave **Session types** empty to see every type, or pick some to narrow the list. When you pick a session, the app loads `/drivers` and **every lap of every driver** (`/laps`) in two requests.
3. The first time you pick a driver, the app downloads that driver's **whole session** of `/location` (x, y position) and `/car_data` (speed). Every lap for that driver is then cut out of memory, so switching laps is instant. The **⚡ Preload all drivers** button does this for everyone in the session up front.
4. Position and speed are sampled at slightly different timestamps, so speed is **interpolated in time** onto each position timestamp.
5. Each lap's data is sent to the browser once, and a small canvas player (`player.html`) animates it at your screen's frame rate.

Data is cached in memory for 6 hours and shared by everyone using the app, so a session someone else already loaded opens instantly for you too.

### Staying inside OpenF1's free limits

OpenF1's free tier allows 3 requests per second and 30 per minute. The app spaces out its requests to stay under that (for all visitors combined) and waits and retries if OpenF1 asks it to slow down. If a whole-session request is too big for OpenF1, it's split into 20-minute pieces automatically.

While data loads, a loading card shows what's being fetched, step by step, with a progress bar when preloading several drivers.

## Notes and limitations

- Loading a driver's first lap takes longer than before (a whole session, up to ~30 s for long races), but every lap after that is instant. Preloading a full grid takes about 1–2 minutes because of OpenF1's 30-requests-per-minute limit.
- Data for very recent or in-progress sessions may be incomplete.
- Streamlit Community Cloud puts apps to sleep when nobody's using them; the first visit after that can take a minute to load.
- The last lap of a session is skipped if OpenF1 has no lap duration for it, because there's no end time to request data up to.
- Position coordinates come straight from OpenF1 and have no units shown; the track shape is correct but not drawn to a real-world scale.
- **OpenF1 will not supply data during live F1 events. The app may not work correctly during those times.**

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app: sidebar, API calls, data prep |
| `player.html` | The animated map player (HTML + JavaScript), filled with lap data by `app.py` |
| `placeholder.html` | The animated circuit shown before a lap is picked |
| `requirements.txt` | Python dependencies |

## Credits

Data: [OpenF1](https://openf1.org), an open-source API for Formula 1 timing and telemetry data. This project is unofficial and not associated with Formula 1 or the FIA.
