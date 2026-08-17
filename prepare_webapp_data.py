"""
prepare_webapp_data.py
======================
Converts Python outputs into web-ready data files for the Leaflet dashboard.

Generates in webapp/data/:
  flood_extent.geojson  – vectorized flood polygons from flood_mask.tif
  safe_route.geojson    – evacuation route (copy/simplify)
  gauge_readings.json   – hourly gauge time series from assimilation_result.csv
  gauge_live.json       – current (latest) gauge reading stub
"""
import os, json, shutil
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes as rio_shapes
from shapely.geometry import shape, mapping, MultiPolygon
from shapely.ops import unary_union
import geopandas as gpd

OUT_DIR = os.path.join("webapp", "data")
os.makedirs(OUT_DIR, exist_ok=True)

# ── 1. Vectorize flood_mask.tif ──────────────────────────────────────────────
print("[1/4] Vectorizing flood_mask.tif …")
with rasterio.open("flood_mask.tif") as src:
    data = src.read(1)
    transform = src.transform
    crs = src.crs

# Collect flood pixels (value == 1); ignore nodata (255)
flood_polys = []
for geom, val in rio_shapes(data, mask=(data == 1).astype(np.uint8),
                             transform=transform):
    if val == 1:
        flood_polys.append(shape(geom))

print(f"   Raw polygons: {len(flood_polys)}")

# Merge into a single MultiPolygon and simplify aggressively for the web
# (flood_mask can have 100k+ tiny polygons – simplify to ~200 KB)
if flood_polys:
    merged = unary_union(flood_polys)
    simplified = merged.simplify(0.002, preserve_topology=True)  # ~200m tolerance
    if simplified.geom_type == "Polygon":
        simplified = MultiPolygon([simplified])

    flood_geojson = {
        "type": "FeatureCollection",
        "name": "Kopili Flood Extent – July 2020",
        "features": [{
            "type": "Feature",
            "geometry": mapping(simplified),
            "properties": {
                "description": "HAND-based flood extent, peak discharge 1473 m3/s",
                "date": "2020-07-29",
                "depth_threshold_m": 0.3
            }
        }]
    }
    out = os.path.join(OUT_DIR, "flood_extent.geojson")
    with open(out, "w") as f:
        json.dump(flood_geojson, f, separators=(",", ":"))
    print(f"   Saved: {out}  ({os.path.getsize(out)/1024:.0f} KB)")
else:
    print("   [WARN] No flood pixels found – creating empty GeoJSON")
    with open(os.path.join(OUT_DIR, "flood_extent.geojson"), "w") as f:
        json.dump({"type":"FeatureCollection","features":[]}, f)

# ── 2. Copy/simplify evacuation route ────────────────────────────────────────
print("[2/4] Copying evacuation route …")
src_route = "evacuation_route.geojson"
dst_route = os.path.join(OUT_DIR, "safe_route.geojson")
if os.path.exists(src_route):
    shutil.copy(src_route, dst_route)
    print(f"   Saved: {dst_route}  ({os.path.getsize(dst_route)/1024:.1f} KB)")
else:
    # Create a synthetic straight-line route as fallback
    route_stub = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[92.85, 26.00], [92.82, 26.05],
                                [92.80, 26.10], [92.78, 26.13]]
            },
            "properties": {"status": "PASSABLE", "road_name": "NH-27 Bypass",
                           "length_m": 15200}
        }]
    }
    with open(dst_route, "w") as f:
        json.dump(route_stub, f, indent=2)
    print(f"   [FALLBACK] Synthetic route saved: {dst_route}")

# ── 3. Extract hourly gauge time series ──────────────────────────────────────
print("[3/4] Extracting gauge time series from assimilation_result.csv …")
df = pd.read_csv("assimilation_result.csv", parse_dates=["datetime"])

# Sample every 4th row (hourly from 15-min data) to keep JSON small (~180 rows)
df_hourly = df.iloc[::4].copy()
df_hourly["alert"] = df_hourly["alert_level"].map(
    {0: "NORMAL", 1: "WARNING", 2: "DANGER", 3: "EXTREME"})
df_hourly["alert_color"] = df_hourly["alert_level"].map(
    {0: "#22c55e", 1: "#facc15", 2: "#f97316", 3: "#ef4444"})

readings = []
for _, row in df_hourly.iterrows():
    readings.append({
        "t": row["datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
        "stage_m": round(row["kf_estimate_m"], 3),
        "observed_m": round(row["observed_m"], 3),
        "true_m": round(row["true_stage_m"], 3),
        "alert": row["alert"],
        "alert_color": row["alert_color"],
        "alert_level": int(row["alert_level"])
    })

gauge_series = {
    "gauge_id": "KPL-001",
    "gauge_name": "Kopili at Umrangso",
    "lat": 25.967,
    "lon": 92.951,
    "datum_m": 0.30,
    "warning_m": 5.5,
    "danger_m": 7.0,
    "extreme_m": 9.0,
    "unit": "m",
    "readings": readings
}

out = os.path.join(OUT_DIR, "gauge_readings.json")
with open(out, "w") as f:
    json.dump(gauge_series, f, separators=(",", ":"))
print(f"   Saved: {out}  ({os.path.getsize(out)/1024:.0f} KB, {len(readings)} hourly readings)")

# ── 4. Write "live" gauge stub (latest reading only) ─────────────────────────
print("[4/4] Writing gauge_live.json stub …")
latest = readings[-1]
live = {
    "gauge_id": "KPL-001",
    "gauge_name": "Kopili at Umrangso",
    "lat": 25.967,
    "lon": 92.951,
    "stage_m": latest["stage_m"],
    "alert": latest["alert"],
    "alert_color": latest["alert_color"],
    "alert_level": latest["alert_level"],
    "timestamp": latest["t"],
    "trend": "FALLING",
    "quality": "GOOD"
}
out = os.path.join(OUT_DIR, "gauge_live.json")
with open(out, "w") as f:
    json.dump(live, f, indent=2)
print(f"   Saved: {out}")

print("\nAll data files ready in webapp/data/")
print("Next: open webapp/index.html in a browser (or run a local server).")
