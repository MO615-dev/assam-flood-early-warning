"""
rainfall_runoff.py
==================
Step 2: Rainfall-Runoff Modelling for Kopili Basin, Assam
----------------------------------------------------------
What this script does (step by step):
  1. Downloads daily CHIRPS v2.0 rainfall GeoTIFFs for July 2020
     (CHIRPS = Climate Hazards Group InfraRed Precipitation with Station data;
      freely available at ~5 km resolution, no login needed)
  2. Clips each daily raster to the Kopili basin bounding box
  3. Computes a basin-averaged daily rainfall (mm)
  4. Applies the SCS Curve Number (CN) rainfall-runoff model to get
     daily direct-runoff depth (mm)
  5. Converts runoff depth → discharge (m³/s) using a simple
     triangular unit-hydrograph approach
  6. Adds a baseflow component with an exponential recession constant
  7. Plots the hydrograph (rainfall bar + discharge line)
  8. Saves daily discharge to  discharge_2020.csv

Bounding box: lon 92.0–93.5, lat 25.5–26.5  (Kopili River, Assam)

Dependencies:
    pip install rasterio numpy pandas matplotlib requests tqdm
"""

import os
import sys
import gzip
import shutil
import tempfile
import datetime
import warnings

import requests
import numpy as np
import pandas as pd
import matplotlib
if "--headless" in sys.argv:
    matplotlib.use("Agg")
else:
    try:
        matplotlib.use("TkAgg")
    except Exception:
        matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import box as shapely_box
from tqdm import tqdm

warnings.filterwarnings("ignore")

# Force UTF-8 console output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Study area – Kopili River basin, Assam
BBOX = (92.0, 25.5, 93.5, 26.5)        # (min_lon, min_lat, max_lon, max_lat)

# Approximate basin area (km²).
# Kopili River drains ~15,225 km² up to Umrangso gauge.
# We use the fraction that falls inside our bounding box (~12,000 km²).
BASIN_AREA_KM2 = 12_000.0

# SCS Curve Number settings
#   CN = 85  → humid basin with mixed forests, cropland, and settled areas
#   Typical range for Assam: 80–90
CN = 85

# Baseflow recession constant (dimensionless, 0 < k < 1)
#   k = 0.85 means baseflow decreases by 15% per day without recharge
RECESSION_K = 0.85

# Initial baseflow at start of simulation (m³/s)
#   Typical dry-season baseflow for Kopili is ~50–80 m³/s
INITIAL_BASEFLOW = 60.0

# Time period
YEAR = 2020
MONTH = 7   # July

# CHIRPS data directory (tiles cached here)
CHIRPS_DIR = "chirps_data"

# Output files
OUT_CSV = "discharge_2020.csv"
OUT_PLOT = "hydrograph_2020.png"

# CHIRPS base URL (global daily, 0.05° resolution)
CHIRPS_BASE_URL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/"
    "global_daily/tifs/p05/{year}/"
    "chirps-v2.0.{year}.{month:02d}.{day:02d}.tif.gz"
)


# =============================================================================
# STEP 1 – DOWNLOAD CHIRPS DAILY RAINFALL GEOTIFFS
# =============================================================================

def get_chirps_filename(year: int, month: int, day: int) -> str:
    """Return local path for a CHIRPS daily tile."""
    os.makedirs(CHIRPS_DIR, exist_ok=True)
    return os.path.join(CHIRPS_DIR, f"chirps_{year}{month:02d}{day:02d}.tif")


def download_chirps_day(year: int, month: int, day: int) -> str | None:
    """
    Download a single CHIRPS daily GeoTIFF (gzipped) and decompress it.
    Returns local .tif path, or None on failure.
    """
    local_tif = get_chirps_filename(year, month, day)
    if os.path.exists(local_tif):
        return local_tif  # already cached

    url = CHIRPS_BASE_URL.format(year=year, month=month, day=day)
    local_gz = local_tif + ".gz"

    try:
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(local_gz, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                fh.write(chunk)
        # Decompress .gz → .tif
        with gzip.open(local_gz, "rb") as gz_in, open(local_tif, "wb") as tif_out:
            shutil.copyfileobj(gz_in, tif_out)
        os.remove(local_gz)
        return local_tif
    except Exception as exc:
        print(f"  [WARN] Could not download {url}: {exc}")
        if os.path.exists(local_gz):
            os.remove(local_gz)
        return None


def download_chirps_month(year: int, month: int) -> list[tuple]:
    """
    Download all daily CHIRPS tiles for a given year/month.
    Returns list of (date, local_tif_path) tuples.
    """
    import calendar
    _, n_days = calendar.monthrange(year, month)
    dates = [datetime.date(year, month, d + 1) for d in range(n_days)]

    results = []
    print(f"\n[CHIRPS] Downloading daily rainfall tiles for {year}-{month:02d} …")
    for date in tqdm(dates, desc="CHIRPS tiles", ncols=70):
        path = download_chirps_day(date.year, date.month, date.day)
        results.append((date, path))
    return results


# =============================================================================
# STEP 2 – BASIN-AVERAGED RAINFALL
# =============================================================================

def extract_basin_mean_rainfall(tif_path: str, bbox: tuple) -> float:
    """
    Read a CHIRPS GeoTIFF, clip to bbox, return mean rainfall in mm.
    CHIRPS no-data value is -9999; these are masked before averaging.
    """
    geom = [shapely_box(*bbox).__geo_interface__]
    try:
        with rasterio.open(tif_path) as src:
            clipped, _ = rasterio_mask(src, geom, crop=True, filled=False)
            data = clipped[0]
            # Mask nodata (-9999) and any negative values
            valid = data[(data != src.nodata) & (data >= 0)]
            if valid.size == 0:
                return 0.0
            return float(valid.mean())
    except Exception as exc:
        print(f"  [WARN] Could not read {tif_path}: {exc}")
        return 0.0


# =============================================================================
# STEP 3 – SCS CURVE NUMBER MODEL
# =============================================================================

def scs_runoff_depth(rainfall_mm: float, cn: float) -> float:
    """
    Compute direct-runoff depth (mm) from daily rainfall using the
    SCS Curve Number method.

    Equations:
        S  = (25400 / CN) - 254          [potential maximum retention, mm]
        Ia = 0.2 * S                      [initial abstraction, mm]
        Q  = (P - Ia)^2 / (P - Ia + S)  [runoff depth, mm]  when P > Ia
        Q  = 0                            when P <= Ia

    References:
        USDA-NRCS (2004), National Engineering Handbook, Part 630, Ch. 10
    """
    S = (25400.0 / cn) - 254.0   # potential retention (mm)
    Ia = 0.2 * S                  # initial abstraction (mm)

    if rainfall_mm <= Ia:
        return 0.0

    Q = (rainfall_mm - Ia) ** 2 / (rainfall_mm - Ia + S)
    return max(Q, 0.0)


# =============================================================================
# STEP 4 – RUNOFF DEPTH → DISCHARGE (m³/s)
# =============================================================================

def depth_to_discharge(runoff_depth_mm: float, area_km2: float,
                        time_seconds: float = 86400.0) -> float:
    """
    Convert runoff depth (mm) over a basin area (km²) to average
    discharge over a time step (m³/s).

    Q_m3s = (depth_m * area_m2) / time_s

    We assume the daily runoff is distributed uniformly across the day
    (a simplification; a unit-hydrograph would spread it over 2–3 days).
    """
    area_m2 = area_km2 * 1e6           # km² → m²
    depth_m = runoff_depth_mm / 1000.0  # mm  → m
    discharge = (depth_m * area_m2) / time_seconds
    return discharge


# =============================================================================
# STEP 5 – BASEFLOW RECESSION MODEL
# =============================================================================

def apply_baseflow_recession(surface_discharge: list[float],
                              initial_baseflow: float,
                              recession_k: float) -> list[float]:
    """
    Add a simple linear (exponential) baseflow recession component.

    Model:
        BF(t) = BF(t-1) * k  +  recharge(t)

    where recharge is assumed proportional to the surface runoff
    (30% of direct runoff seeps back as delayed baseflow).

    Total discharge:
        Q_total(t) = Q_surface(t) + BF(t)

    recession_k: 0.85 means baseflow halves every ~4.3 days with no recharge
    """
    bf = initial_baseflow
    total = []
    for qs in surface_discharge:
        # Baseflow recharge: a fraction of surface runoff represents
        # sub-surface contribution arriving with a lag
        recharge = 0.30 * qs
        bf = bf * recession_k + recharge
        total.append(qs + bf)
    return total


# =============================================================================
# STEP 6 – PLOT HYDROGRAPH
# =============================================================================

def plot_hydrograph(dates: list, rainfall: list[float],
                    discharge: list[float], out_path: str) -> None:
    """
    Plot a dual-axis hydrograph:
      - Top (inverted): daily rainfall bar chart
      - Bottom: computed discharge line
    """
    fig, (ax_rain, ax_q) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True,
        gridspec_kw={"height_ratios": [1, 2], "hspace": 0.08}
    )
    fig.patch.set_facecolor("#0f172a")
    for ax in (ax_rain, ax_q):
        ax.set_facecolor("#1e293b")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#334155")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")

    # --- Rainfall bars (inverted axis) ---
    ax_rain.bar(dates, rainfall, color="#38bdf8", alpha=0.85,
                label="Daily Rainfall (mm)", width=0.9, zorder=3)
    ax_rain.invert_yaxis()
    ax_rain.set_ylabel("Rainfall (mm)", color="#38bdf8", fontsize=10)
    ax_rain.tick_params(axis="y", labelcolor="#38bdf8")
    ax_rain.set_title(
        "Kopili Basin – Hydrograph  |  July 2020\n"
        "SCS-CN Model  (CN=85, Baseflow k=0.85)",
        fontsize=12, fontweight="bold", color="white", pad=10
    )
    ax_rain.legend(loc="lower right", facecolor="#1e293b",
                   edgecolor="#334155", labelcolor="white", fontsize=9)
    ax_rain.grid(axis="y", color="#334155", linestyle="--", alpha=0.5)

    # --- Discharge line ---
    ax_q.fill_between(dates, discharge, alpha=0.25, color="#f97316")
    ax_q.plot(dates, discharge, color="#f97316", linewidth=2.0,
              marker="o", markersize=4, label="Total Discharge (m³/s)")
    ax_q.set_ylabel("Discharge (m³/s)", color="#f97316", fontsize=10)
    ax_q.tick_params(axis="y", labelcolor="#f97316")
    ax_q.set_xlabel("Date", color="white", fontsize=10)
    ax_q.legend(loc="upper left", facecolor="#1e293b",
                edgecolor="#334155", labelcolor="white", fontsize=9)
    ax_q.grid(color="#334155", linestyle="--", alpha=0.5)

    # Peak annotation
    peak_idx = int(np.argmax(discharge))
    ax_q.annotate(
        f"Peak: {discharge[peak_idx]:.0f} m³/s",
        xy=(dates[peak_idx], discharge[peak_idx]),
        xytext=(dates[peak_idx], discharge[peak_idx] * 1.05),
        color="white", fontsize=9,
        arrowprops=dict(arrowstyle="->", color="white"),
        ha="center"
    )

    ax_q.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax_q.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    plt.xticks(rotation=30, color="white")

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"  [SAVED] Hydrograph plot -> {out_path}")
    if matplotlib.get_backend().lower() != "agg":
        plt.show()


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def main():
    print("\n" + "=" * 62)
    print("  Kopili Basin - Rainfall-Runoff Model  |  July 2020")
    print("=" * 62)
    print(f"  Bounding box   : lon {BBOX[0]}-{BBOX[2]}, lat {BBOX[1]}-{BBOX[3]}")
    print(f"  Basin area     : {BASIN_AREA_KM2:,.0f} km2")
    print(f"  Curve Number   : {CN}")
    print(f"  Recession k    : {RECESSION_K}")
    print(f"  Initial BF     : {INITIAL_BASEFLOW} m3/s")

    # ------------------------------------------------------------------
    # STEP 1: Download CHIRPS daily tiles for July 2020
    # ------------------------------------------------------------------
    print("\n--- STEP 1: Download CHIRPS Rainfall Data ---")
    tile_records = download_chirps_month(YEAR, MONTH)

    # ------------------------------------------------------------------
    # STEP 2: Compute basin-averaged daily rainfall (mm)
    # ------------------------------------------------------------------
    print("\n--- STEP 2: Computing Basin-Averaged Rainfall ---")
    dates = []
    rainfall_mm = []

    for date, tif_path in tile_records:
        if tif_path is None or not os.path.exists(tif_path):
            print(f"  [WARN] Missing tile for {date} — substituting 0 mm")
            mean_rain = 0.0
        else:
            mean_rain = extract_basin_mean_rainfall(tif_path, BBOX)

        dates.append(date)
        rainfall_mm.append(mean_rain)
        print(f"  {date}  ->  {mean_rain:.2f} mm")

    # ------------------------------------------------------------------
    # STEP 3: Apply SCS-CN model to get daily runoff depth (mm)
    # ------------------------------------------------------------------
    print("\n--- STEP 3: SCS-CN Runoff Calculation ---")
    S = (25400.0 / CN) - 254.0
    Ia = 0.2 * S
    print(f"  S (potential retention) = {S:.1f} mm")
    print(f"  Ia (initial abstraction) = {Ia:.1f} mm")
    print(f"  Runoff only generated when P > {Ia:.1f} mm/day")

    runoff_depth_mm = [scs_runoff_depth(p, CN) for p in rainfall_mm]

    for date, p, q in zip(dates, rainfall_mm, runoff_depth_mm):
        print(f"  {date}  P={p:5.1f} mm  ->  Q_runoff={q:5.1f} mm")

    # ------------------------------------------------------------------
    # STEP 4: Convert runoff depth (mm) → surface discharge (m³/s)
    # ------------------------------------------------------------------
    print("\n--- STEP 4: Depth-to-Discharge Conversion ---")
    surface_q = [depth_to_discharge(q, BASIN_AREA_KM2) for q in runoff_depth_mm]
    print(f"  Area used : {BASIN_AREA_KM2:,.0f} km2")
    print(f"  Time step : 86,400 s (1 day)")

    # ------------------------------------------------------------------
    # STEP 5: Add baseflow recession
    # ------------------------------------------------------------------
    print("\n--- STEP 5: Adding Baseflow Recession Component ---")
    total_q = apply_baseflow_recession(surface_q, INITIAL_BASEFLOW, RECESSION_K)

    # ------------------------------------------------------------------
    # STEP 6: Save results as CSV
    # ------------------------------------------------------------------
    print(f"\n--- STEP 6: Saving Results to {OUT_CSV} ---")
    df = pd.DataFrame({
        "date": dates,
        "rainfall_mm": [round(p, 3) for p in rainfall_mm],
        "runoff_depth_mm": [round(q, 3) for q in runoff_depth_mm],
        "surface_discharge_m3s": [round(qs, 3) for qs in surface_q],
        "total_discharge_m3s": [round(qt, 3) for qt in total_q],
    })
    df.to_csv(OUT_CSV, index=False)
    print(df.to_string(index=False))
    print(f"\n  [SAVED] -> {OUT_CSV}")

    # Summary stats
    print("\n--- SUMMARY STATISTICS ---")
    print(f"  Total rainfall        : {sum(rainfall_mm):.1f} mm")
    print(f"  Total runoff depth    : {sum(runoff_depth_mm):.1f} mm")
    print(f"  Runoff coefficient    : {sum(runoff_depth_mm)/max(sum(rainfall_mm),0.01):.3f}")
    print(f"  Peak discharge        : {max(total_q):.1f} m3/s  on {dates[int(np.argmax(total_q))]}")
    print(f"  Mean discharge (July) : {np.mean(total_q):.1f} m3/s")

    # ------------------------------------------------------------------
    # STEP 7: Plot hydrograph
    # ------------------------------------------------------------------
    print(f"\n--- STEP 7: Plotting Hydrograph -> {OUT_PLOT} ---")
    plot_hydrograph(dates, rainfall_mm, total_q, OUT_PLOT)

    print("\n" + "=" * 62)
    print("  Done! Outputs:")
    print(f"    {OUT_CSV}")
    print(f"    {OUT_PLOT}")
    print("=" * 62)


if __name__ == "__main__":
    main()
