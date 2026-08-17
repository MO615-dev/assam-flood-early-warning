# Kopili Basin Flood Modeling and Route Planning Prototype

**Document type:** Educational technical concept note
**Date:** August 2026

> This prototype is not affiliated with or endorsed by ASDMA, the Government of Assam, or any emergency-response organization. Its modeled flood extents, gauge values, routes, service locations and response timeline are illustrative and are not validated for operational use. Do not use them for emergency decisions.

---

## 1. Executive Overview

The state of Assam, specifically the Kopili River Basin, faces recurrent and devastating monsoon floods that result in significant loss of life, displacement of communities, and severe economic disruption. Traditional flood warning systems rely heavily on expensive, proprietary software, localized sparse sensor networks, and high-performance computing infrastructure that often lacks the agility required for real-time, hyperlocal emergency response. 

This concept note describes a **proof-of-concept workflow** for exploring flood inundation, example gauge assimilation and route analysis in the Kopili River Basin. It uses open-source geospatial tools and public datasets where available. The current dashboard is a static demonstration with prepared and simulated inputs; it does not ingest live sensor data, issue warnings, or validate safe routes. Any operational use would require authoritative inputs, field validation, uncertainty analysis, and review by qualified local agencies.

## 2. The Cost-Effective "Digital Twin" Approach

A future digital twin would update a virtual representation of the basin as new data arrives. This repository is a static prototype and does not yet provide that live connection.

### 2.1 Free and Open Data Sources
The system eliminates data acquisition costs by utilizing global public goods:
* **Topography:** High-resolution Copernicus GLO-30 Digital Elevation Models (DEM).
* **Precipitation:** CHIRPS rainfall data used by the example workflow; the hosted dashboard does not ingest live rainfall.
* **Hydrography:** HydroSHEDS river centerlines for accurate stream network topology.
* **Infrastructure:** OpenStreetMap (OSM) for up-to-date road networks and village locations.

### 2.2 Lightweight, Open-Source Compute
The core engine relies on the **Height Above Nearest Drainage (HAND)** methodology. Unlike computationally intensive 2D hydrodynamic models that require solving complex Navier-Stokes equations over days, the HAND model uses rapid geospatial distance transforms. This allows the system to generate high-resolution flood inundation maps in a matter of seconds on a standard commercial laptop. The entire software stack is built on free, open-source Python libraries (`rasterio`, `scipy`, `geopandas`), requiring zero licensing fees.

## 3. Demonstrated Methods and Limits

The prototype illustrates ways environmental data could be processed. Its outputs are not actionable intelligence and must not be used by first responders or the public.

### 3.1 Simulated Data Assimilation
In the chaos of a flood event, physical river gauges are prone to failure, telemetry dropouts, or debris strikes. To ensure the reliability of the early warning system, we have integrated a **1D Kalman Filter** for real-time data assimilation. 
* **The Mechanism:** The system mathematically fuses incoming (and potentially noisy) sensor readings with a continuous background predictive model. 
* **Example result:** In the included simulated July 2020 run, the reported RMSE was about **59% lower** after filtering than for the uncorrected model. This is a result from one synthetic scenario, not independent validation or evidence of improved warning accuracy in real floods.

### 3.2 Dynamic, Flood-Aware Evacuation Routing
Simply knowing that an area will flood is insufficient; residents must know how to escape safely. 
* **Hyperlocal Risk Assessment:** The system automatically overlays the modeled flood depth raster onto the rural road network. 
* **Automated Routing:** Using shortest-path algorithms (Dijkstra), the system calculates the optimal route from vulnerable villages to designated flood shelters. Crucially, it assigns infinite traversal cost to any road segment submerged under more than 0.3 meters of water (the safe limit for standard emergency vehicles).
* **Output:** Responders are provided with turn-by-turn directions and a safe route map, drastically reducing the risk of evacuation convoys becoming trapped in rising waters.

## 4. Requirements Before Any Operational Use

The current project is a local prototype. Scaling or operational deployment has not been tested.

* Establish data agreements and use current, authoritative rainfall, river-gauge, road and shelter data.
* Validate inundation and route results against field observations and historical events, with qualified local review.
* Assess uncertainty, accessibility, communications, security, reliability and maintenance before any pilot.
* The included client-side dashboard is a demonstration only; it has no live data feed or operational backend.

## 5. Conclusion and Recommendations

This prototype demonstrates a possible processing workflow, not an operational flood forecasting or evacuation system. Further research and independent validation would be needed before considering a pilot.

**Recommendations for Phase 2 (Operationalization):**
1. **Pilot Deployment:** Transition the Kopili Basin PoC to a live pilot, ingesting live API data from Indian Meteorological Department (IMD) rainfall forecasts and Central Water Commission (CWC) telemetry.
2. **Ground Truthing:** Validate the HAND inundation extents using historical Sentinel-1 Synthetic Aperture Radar (SAR) imagery of past Assam floods.
3. **Local Review:** Consult relevant local agencies and communities before selecting data, thresholds, routes or warning procedures.

By adopting this modern, data-driven approach, Assam can significantly enhance its disaster resilience, optimize its limited emergency resources, and fundamentally protect the lives and livelihoods of its citizens.
