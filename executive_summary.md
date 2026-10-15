# Mathematical Digital Twin for Assam Flood Prediction & Safe Evacuation Routing

**Document type:** Project brief and technical overview
**Geographic focus of the current dashboard:** Kopili River Basin, Assam

> **Status and safety:** This repository contains a mathematical modeling prototype and interactive demonstration. It is not a live digital twin, operational flood forecast, public warning service, or validated evacuation system. Dashboard values and controls may be prepared or simulated. They must not be used to make emergency, travel, or evacuation decisions. Follow official warnings and local authorities.

## 1. Project overview

This project explores how rainfall, terrain, river-stage scenarios and road-network information can be combined to model flood conditions and examine evacuation-route scenarios in Assam. Its broader concept is a mathematical digital twin: a virtual representation of a river basin that could be updated as new observations become available. The current implementation is an early prototype. Its dashboard presents prepared data and simulations; it does not connect to live sensor feeds or maintain a continuously synchronized representation of the basin.

The project combines a Python geospatial workflow with a browser dashboard. Its current geographic focus is the Kopili River Basin, while the title reflects the broader Assam flood-risk problem the project is intended to explore.

## 2. Processing concept

```text
Rainfall and terrain inputs → runoff and water-level scenarios → HAND flood-depth layers → road-network route scenarios → dashboard
```

The workflow represents several mathematical and geospatial methods:

1. Estimate runoff from rainfall using the SCS Curve Number method.
2. Use terrain and drainage information for HAND-based flood-depth analysis.
3. Demonstrate gauge-data assimilation with a Kalman-filter workflow.
4. Represent roads as a graph and explore routes under flood-depth thresholds.
5. Display prepared layers, charts and scenarios in an interactive dashboard.

The dashboard is a static site hosted on GitHub Pages. Its controls do not provide live monitoring, issue authoritative warnings, transmit SMS, contact emergency responders, or confirm that a route or shelter is safe.

## 3. Inputs and data

The scripts reference public geospatial sources such as CHIRPS rainfall, Copernicus DEM, HydroSHEDS hydrography and OpenStreetMap infrastructure. Individual datasets can differ in coverage, date, resolution, quality and license. Prepared dashboard examples should not be treated as current measurements unless their provenance and timestamp establish that.

## 4. How to interpret the results

Maps and charts illustrate what the selected inputs, assumptions and thresholds produce. They do not by themselves demonstrate predictive accuracy or operational readiness. Flood extent depends on the terrain model, drainage representation, water-level assumptions and processing choices. Route scenarios depend on the road data and flood-depth thresholds and are not field-verified evacuation guidance.

No claim of lives saved, 3–5 days of warning, independent accuracy, or validation against current CWC observations is made by this brief. Any such claim would require traceable source records, reproducible evaluation and independent review.

## 5. Requirements before real-world use

Further work would be required before considering operational use:

- Ingest current, authoritative rainfall, gauge, road and shelter data with clear provenance.
- Calibrate and validate model outputs against independent historical observations and field evidence.
- Quantify uncertainty and document spatial and temporal limits.
- Verify routes and facilities with local authorities and communities.
- Assess reliability, accessibility, communications, cybersecurity and ongoing maintenance.
- Establish review and approval by qualified agencies responsible for flood warnings and evacuation.

## 6. Project direction

The prototype is intended as a foundation for exploring a broader mathematical digital twin for Assam flood prediction and evacuation-route analysis. Future development could investigate live data assimilation, additional river basins, mobile-friendly access and improved model validation. These are future possibilities, not capabilities currently delivered by this repository.
