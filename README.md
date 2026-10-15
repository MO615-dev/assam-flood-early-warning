# Mathematical Digital Twin for Assam Flood Prediction & Safe Evacuation Routing

A software project exploring how rainfall, terrain, river-stage scenarios and road networks can be combined to model flood conditions and examine evacuation routes in Assam. The current geographic focus of the bundled dashboard and route data is the Kopili River Basin.

The project brings together a Python geospatial processing workflow and an interactive browser dashboard. It includes rainfall-to-runoff calculations, HAND-based flood-depth mapping, an example gauge-assimilation workflow, and graph-based route analysis.

> **Scope and safety:** This is a research and demonstration system, not an operational digital twin, flood forecast, public warning service, or authority-approved evacuation tool. The hosted dashboard uses prepared data and simulated controls; it does not ingest live rainfall or gauge feeds, contact responders, send SMS, or verify that a route or shelter is safe. Outputs are not validated for emergency decisions. Follow official warnings and local authorities.

## Project brief

The goal is to demonstrate a mathematical workflow for turning environmental and infrastructure data into flood-risk visualizations and route scenarios. The long-term concept is a digital twin that could update as authoritative observations arrive. This repository is an early prototype: it processes prepared inputs and presents example scenarios, but does not maintain a live synchronized model.

The intended workflow is:

```text
Rainfall and terrain inputs → runoff and water-level scenarios → HAND flood-depth map → road-network analysis → interactive dashboard
```

The methods are useful for exploring assumptions and communicating a concept. Their presence does not establish forecast accuracy, validated inundation, or safe routes. The model extent, inputs, calibration and results must be independently checked before any real-world use.

## Live dashboard

The static dashboard is hosted on GitHub Pages: **[Open the project](https://mo615-dev.github.io/assam-flood-early-warning/)**.

Pages serves the browser interface and bundled sample data from `webapp/`. It does not run the Python processing pipeline or local server. Some map layers require an internet connection and an external map provider.

## Methods represented

- **Rainfall to runoff:** SCS Curve Number calculations in the Python workflow.
- **Terrain-based inundation:** Height Above Nearest Drainage (HAND) analysis and example flood-depth layers.
- **Gauge assimilation:** A Kalman-filter demonstration using prepared or synthetic observations.
- **Route analysis:** Road-network graph processing and flood-threshold-based route scenarios.
- **Dashboard:** Map layers, charts, timeline controls, example alerts and route visualization.

These are simplified methods and scenarios. The dashboard should not be interpreted as a live operational service or as proof that routes are passable.

## Repository layout

| Path | Purpose |
| --- | --- |
| `webapp/` | Static dashboard and prepared data deployed to GitHub Pages |
| `webapp/server.py` | Optional local-only server for simulated alert interactions |
| `run_full_pipeline.py` | Runs the Python processing phases in sequence |
| `download_dem_rivers.py`, `rainfall_runoff.py`, `flood_hand.py` | Input preparation, runoff and HAND processing |
| `sensor_assimilation.py`, `flood_routing.py` | Gauge-assimilation example and route analysis |
| `prepare_*.py` | Prepares outputs consumed by the dashboard |
| `.github/workflows/pages.yml` | Publishes `webapp/` to Pages when changes reach `main` |

Large source rasters, downloaded hydrography and caches are excluded from Git when they can be regenerated. Prepared dashboard files are included so the hosted interface can load without running the full pipeline.

## Run the dashboard locally

Python 3 can serve the static files:

```bash
python -m http.server 8080 --directory webapp
```

Then open <http://localhost:8080>.

## Run the processing workflow

Python 3.10 or later is recommended. Geospatial libraries may require platform-specific dependencies; on Windows, a Conda environment using `conda-forge` is often convenient.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python run_full_pipeline.py
```

Some stages download external datasets and may need an internet connection, disk space and memory. Check provider terms and attribution before redistributing downloaded data.

## Data and limitations

The workflow references public sources including CHIRPS rainfall, Copernicus DEM, HydroSHEDS hydrography and OpenStreetMap infrastructure. Availability, coverage, licensing and attribution vary by provider. Dashboard sample data and simulated readings should be distinguished from current observations. The repository does not establish independent validation against authoritative gauges or observed flood extents.

Before any operational consideration, the project would need current authoritative inputs, documented calibration, uncertainty analysis, independent historical and field validation, route and shelter verification, security and reliability work, and review by qualified local agencies and communities.

## Deployment

GitHub Actions publishes `webapp/` to GitHub Pages when changes are pushed to `main` or the workflow is run manually. The workflow is in `.github/workflows/pages.yml`. The static host does not deploy the local Python endpoints.

## License

No license has been selected. Public visibility alone does not grant permission to reuse the code. Data sources may have separate terms.
