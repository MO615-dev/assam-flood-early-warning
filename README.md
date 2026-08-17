# Kopili Flood Response Prototype

An educational prototype exploring how terrain, rainfall and river-stage models can support flood situational awareness and evacuation planning in the Kopili River Basin, Assam. It combines a Python geospatial pipeline with a browser dashboard for exploring modeled flood extents, gauge scenarios and routes.

> **Important:** This is a research and demonstration project. It is not an operational warning service. Dashboard readings, alert messages, flood extents and evacuation routes are modeled or simulated and may be incomplete or wrong. Do not use them to make safety, travel or emergency-response decisions. Follow official alerts and local authorities.

## Demo

The interactive dashboard is published with GitHub Pages: **[Open the dashboard](https://MO615-dev.github.io/assam-flood-early-warning/)**.

The dashboard is a static site. Its simulation controls do not connect to live gauges, emergency services or an SMS provider. The optional local Python server records example alerts locally; it does not transmit messages.

## What it demonstrates

- Rainfall-to-runoff calculations and example discharge series.
- Height Above Nearest Drainage (HAND)-based flood-depth scenarios.
- A Kalman-filter gauge-assimilation example with synthetic observations.
- Road-network analysis and modeled evacuation-route examples.
- A Leaflet dashboard with map layers, charts and timeline controls.

These are proof-of-concept methods and example outputs, not validated forecasts. Results depend on input data, model assumptions, spatial resolution and thresholds.

## Repository layout

| Path | Purpose |
| --- | --- |
| `webapp/` | Static dashboard, bundled map/chart libraries and sample data deployed to Pages |
| `webapp/server.py` | Optional local-only server for the desktop and mobile alert simulation |
| `run_full_pipeline.py` | Runs the Python processing phases in sequence |
| `download_dem_rivers.py`, `rainfall_runoff.py`, `flood_hand.py` | Data acquisition, runoff and HAND modeling |
| `sensor_assimilation.py`, `flood_routing.py` | Gauge-assimilation demonstration and route analysis |
| `prepare_*.py` | Builds data consumed by the dashboard |
| `data/`, `*.csv`, `*.geojson`, `*.png` | Prepared examples, reports and visual outputs |
| `.github/workflows/pages.yml` | Publishes `webapp/` to GitHub Pages on pushes to `main` |

Downloaded rasters, source hydrography and cache files are excluded from Git because they can be large and can be regenerated. The dashboard's prepared sample files are included.

## Run locally

### Dashboard only

Python 3 is enough to serve the static dashboard:

```bash
python -m http.server 8080 --directory webapp
```

Open <http://localhost:8080>.

### Full processing pipeline

Python 3.10 or later is recommended. Geospatial packages such as Rasterio and GeoPandas may need platform-specific system libraries; on Windows, a Conda environment using `conda-forge` is often the easiest setup.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python run_full_pipeline.py
```

The pipeline downloads some inputs and can take several minutes. It may require an internet connection and adequate disk space and memory. Review the source scripts and data-provider terms before reusing downloaded data.

### Optional local alert demo

```bash
python webapp/server.py
```

The local server listens on ports 8080 and 8081. Its alerts are examples stored on the machine running the server; no SMS is sent. Avoid exposing this development server to an untrusted network.

## Data and methods

The scripts reference Copernicus DEM via Microsoft Planetary Computer, HydroSHEDS HydroRIVERS, CHIRPS rainfall and OpenStreetMap roads. The dashboard includes prepared example files so the map can load without running the pipeline. Some displayed time series and sensor readings are simulated. Source data can have separate licenses and attribution requirements; consult each provider before redistribution.

The modeling workflow includes SCS Curve Number runoff estimation, HAND terrain analysis, a one-dimensional Kalman-filter example, and graph-based route selection. These methods are simplified for demonstration and have not been established as operationally accurate for emergency use.

## Deployment

GitHub Actions deploys the contents of `webapp/` to GitHub Pages when a change is pushed to `main` or the workflow is manually started. The workflow is defined in `.github/workflows/pages.yml`. Pages hosts only the static front end; the local Python endpoints are not deployed there.

## Contributing

Please open an issue describing a bug or proposed change before making a large change. Keep data provenance, assumptions and simulation status clear in the dashboard and documentation. Never commit credentials, personal contact details, local logs, caches or large source rasters.

## License

No license has been selected yet. Until one is added, standard copyright applies and reuse is not granted by this repository's public visibility.
