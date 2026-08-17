"""
flood_hand.py
=============
HAND-based Flood Inundation Model for the Kopili River Basin, Assam
--------------------------------------------------------------------
Method: Height Above Nearest Drainage (HAND)
  Nobre et al. (2011), Hydrology and Earth System Sciences.

Pipeline (step by step):
  1. Load DEM (dem.tif) and river network (rivers.shp)
  2. Rasterize river centerlines onto the DEM grid -> drainage mask
  3. Compute HAND raster:
       HAND(x,y) = DEM(x,y) - DEM(nearest_drainage_pixel_to_(x,y))
     Using scipy.ndimage.distance_transform_edt with return_indices=True
     to efficiently find the nearest drainage cell for every pixel.
  4. Estimate Water Surface Elevation (WSE) from peak discharge:
       Manning's equation for a wide rectangular channel:
         Q = (1/n) * B * y^(5/3) * S^(1/2)
       Solve for normal depth y, then WSE = bed_elev + y
  5. Flood delineation:
       Pixel is flooded if HAND(x,y) < WSE_rise
       Flood depth = WSE_rise - HAND  (positive where flooded)
  6. Output:
       flood_depth.tif  – continuous flood depth (m), 0 where dry
       flood_mask.tif   – binary inundation mask (1=wet, 0=dry)
  7. Visualize: matplotlib figure with DEM + flood overlay

Dependencies: rasterio, numpy, scipy, geopandas, matplotlib
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import rowcol
from rasterio.crs import CRS
import geopandas as gpd
from scipy.ndimage import distance_transform_edt
import matplotlib
import sys
if "--headless" in sys.argv:
    matplotlib.use("Agg")
else:
    try:
        matplotlib.use("TkAgg")
    except Exception:
        matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch

warnings.filterwarnings("ignore")

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# CONFIGURATION
# =============================================================================

DEM_PATH        = "dem.tif"
RIVERS_PATH     = "rivers.shp"
DISCHARGE_CSV   = "discharge_2020.csv"

OUT_DEPTH       = "flood_depth.tif"
OUT_MASK        = "flood_mask.tif"
OUT_PLOT        = "flood_map.png"

# Manning's equation parameters
# n = Manning's roughness coefficient
#   0.035: natural channel, clean & winding with some pools (Chow 1959, Table 5.6)
MANNINGS_N      = 0.035

# Assumed channel width (m) at the reference cross-section.
# Kopili River main stem is ~80-120 m wide near Umrangso.
# Used only for converting discharge -> water depth.
CHANNEL_WIDTH_M = 100.0

# Channel slope (m/m) – computed from DEM or assumed.
# Will be auto-computed from DEM; fallback = 0.0003 (gentle floodplain)
CHANNEL_SLOPE_FALLBACK = 0.0003

# Nodata fill value
NODATA = -9999.0


# =============================================================================
# STEP 1 – LOAD DATA
# =============================================================================

def load_dem(path: str):
    """Load DEM raster. Returns (data_array, profile, nodata_value)."""
    print(f"  Reading DEM: {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DEM not found: {path}\n"
            "Run download_dem_rivers.py first."
        )
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        nodata = src.nodata if src.nodata is not None else -9999.0
    # Mask nodata pixels with NaN
    data[data == nodata] = np.nan
    print(f"    Shape      : {data.shape}  (rows x cols)")
    print(f"    Elevation  : {np.nanmin(data):.1f} – {np.nanmax(data):.1f} m")
    return data, profile, nodata


def load_rivers(path: str, dem_crs):
    """Load river shapefile, reproject to DEM CRS if needed."""
    print(f"  Reading rivers: {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Rivers not found: {path}\n"
            "Run download_dem_rivers.py first."
        )
    rivers = gpd.read_file(path)
    if rivers.crs is None:
        rivers = rivers.set_crs(epsg=4326)
    if rivers.crs != dem_crs:
        rivers = rivers.to_crs(dem_crs)
    print(f"    Features   : {len(rivers)}")
    print(f"    CRS        : {rivers.crs}")
    return rivers


def get_peak_discharge(csv_path: str) -> float:
    """Read discharge_2020.csv and return the single highest daily discharge."""
    print(f"  Reading discharge: {csv_path}")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Discharge CSV not found: {csv_path}\n"
            "Run rainfall_runoff.py first."
        )
    df = pd.read_csv(csv_path, parse_dates=["date"])
    peak_row = df.loc[df["total_discharge_m3s"].idxmax()]
    q_peak = float(peak_row["total_discharge_m3s"])
    date_peak = peak_row["date"]
    print(f"    Peak discharge : {q_peak:.1f} m3/s  on  {date_peak.date()}")
    return q_peak


# =============================================================================
# STEP 2 – RASTERIZE RIVER NETWORK -> DRAINAGE MASK
# =============================================================================

def rasterize_rivers(rivers: gpd.GeoDataFrame, profile: dict) -> np.ndarray:
    """
    Burn river centerlines into a boolean raster (True = drainage pixel).
    Uses rasterio.features.rasterize on the DEM's transform and shape.
    """
    print("  Rasterizing river network onto DEM grid ...")
    height = profile["height"]
    width  = profile["width"]
    transform = profile["transform"]

    shapes = [(geom, 1) for geom in rivers.geometry if geom is not None]
    if not shapes:
        raise RuntimeError("No valid river geometries to rasterize.")

    mask = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=True,   # include every pixel the line passes through
    ).astype(bool)

    n_drain = int(mask.sum())
    print(f"    Drainage pixels: {n_drain:,}  ({100*n_drain/(height*width):.2f}% of raster)")
    return mask


# =============================================================================
# STEP 3 – COMPUTE HAND RASTER
# =============================================================================

def compute_hand(dem: np.ndarray, drainage_mask: np.ndarray) -> np.ndarray:
    """
    Compute Height Above Nearest Drainage (HAND).

    Algorithm:
    ----------
    1. scipy.ndimage.distance_transform_edt(~drainage_mask, return_indices=True)
       returns, for every cell, the row/col of the NEAREST drainage cell.
    2. HAND = DEM[pixel] - DEM[nearest_drainage_pixel]

    This is O(N) where N = number of pixels — very fast even for large DEMs.
    HAND is set to NaN where DEM is NaN (nodata).

    Reference:
        Nobre et al. (2011). Height Above the Nearest Drainage – a hydrologically
        relevant new terrain model. Journal of Hydrology, 404(1-2), 13-29.
    """
    print("  Computing HAND (Height Above Nearest Drainage) ...")

    # Invert mask: distance_transform_edt works on background (False) pixels
    non_drain = ~drainage_mask

    # handle edge case: if no drainage pixels, HAND = all zeros
    if drainage_mask.sum() == 0:
        print("    [WARN] No drainage pixels found — HAND will be all zeros.")
        hand = np.zeros_like(dem)
        hand[np.isnan(dem)] = np.nan
        return hand

    # Compute nearest-drainage indices for every pixel.
    # distance_transform_edt with return_indices returns:
    #   dist  : Euclidean distance to nearest 0 (drainage) cell
    #   inds  : (row_index_array, col_index_array) of nearest drainage cell
    _, inds = distance_transform_edt(non_drain, return_indices=True)
    nearest_row = inds[0]   # shape (H, W)
    nearest_col = inds[1]   # shape (H, W)

    # Elevation of the nearest drainage pixel for each cell
    drain_elev = dem[nearest_row, nearest_col]

    # HAND = local elevation minus nearest-drainage elevation
    hand = dem - drain_elev

    # Pixels already on drainage = HAND of 0
    hand[drainage_mask] = 0.0

    # Propagate NaN for nodata cells
    hand[np.isnan(dem)] = np.nan

    # HAND should be >= 0 everywhere (terrain above drainage)
    # Negative values can appear from DEM artifacts; clamp to 0
    hand = np.where(np.isnan(hand), np.nan, np.clip(hand, 0, None))

    print(f"    HAND range : 0 – {np.nanmax(hand):.1f} m")
    print(f"    Mean HAND  : {np.nanmean(hand):.1f} m")
    return hand.astype(np.float32)


# =============================================================================
# STEP 4 – WATER SURFACE ELEVATION FROM DISCHARGE (MANNING'S EQUATION)
# =============================================================================

def estimate_channel_slope(dem: np.ndarray, drainage_mask: np.ndarray,
                            profile: dict) -> float:
    """
    Estimate mean channel slope from DEM along drainage pixels.
    Slope = (elev_max - elev_min) / channel_length
    Falls back to CHANNEL_SLOPE_FALLBACK if computation fails.
    """
    drain_elevs = dem[drainage_mask]
    drain_elevs = drain_elevs[~np.isnan(drain_elevs)]
    if len(drain_elevs) < 2:
        return CHANNEL_SLOPE_FALLBACK

    elev_range = float(np.percentile(drain_elevs, 95) - np.percentile(drain_elevs, 5))
    # Approximate channel length: number of drainage pixels * pixel size
    pixel_size_m = abs(profile["transform"].a) * 111_320  # degrees -> metres (~)
    channel_length_m = drainage_mask.sum() * pixel_size_m

    if channel_length_m < 1:
        return CHANNEL_SLOPE_FALLBACK

    slope = elev_range / channel_length_m
    # Sanity clamp: 0.00001 to 0.01
    slope = float(np.clip(slope, 0.00001, 0.01))
    return slope


def manning_normal_depth(discharge_m3s: float, width_m: float,
                          slope: float, n: float) -> float:
    """
    Solve Manning's equation for normal depth (y) in a wide rectangular channel.

    Manning's Q = (1/n) * A * R^(2/3) * S^(1/2)

    For a wide rectangular channel:
        A = width * y
        R (hydraulic radius) ≈ y  (valid when width >> depth)

    Therefore:
        Q = (1/n) * width * y^(5/3) * S^(1/2)
        y = [ Q * n / (width * S^0.5) ]^(3/5)

    Parameters
    ----------
    discharge_m3s : peak discharge (m³/s)
    width_m       : channel width (m)
    slope         : dimensionless channel slope (m/m)
    n             : Manning's roughness coefficient

    Returns
    -------
    y : normal flow depth (m)
    """
    y = (discharge_m3s * n / (width_m * slope ** 0.5)) ** (3.0 / 5.0)
    return float(y)


def compute_wse_rise(dem: np.ndarray, drainage_mask: np.ndarray,
                     discharge_m3s: float, profile: dict) -> float:
    """
    Compute the Water Surface Elevation RISE above channel bed.
    This is used as the flood threshold: pixels with HAND < wse_rise are flooded.

    WSE_rise = y  (normal depth from Manning's)

    The HAND framework uses this directly:
        HAND < WSE_rise => flooded
    because HAND already encodes height ABOVE the channel bed elevation,
    so a WSE_rise of y metres means the flood inundates all terrain within
    y metres above the nearest drainage channel.
    """
    slope = estimate_channel_slope(dem, drainage_mask, profile)
    y = manning_normal_depth(discharge_m3s, CHANNEL_WIDTH_M, slope, MANNINGS_N)

    print(f"    Channel slope    : {slope:.6f} m/m")
    print(f"    Manning's n      : {MANNINGS_N}")
    print(f"    Channel width    : {CHANNEL_WIDTH_M} m")
    print(f"    Normal depth (y) : {y:.2f} m")
    print(f"    -> Flood threshold (WSE rise) = {y:.2f} m above channel bed")
    return y


# =============================================================================
# STEP 5 – FLOOD DELINEATION
# =============================================================================

def delineate_flood(hand: np.ndarray, wse_rise: float):
    """
    Determine flooded pixels and compute flood depth.

    Rule:
        flooded  = HAND < wse_rise
        depth    = wse_rise - HAND   (metres above ground surface)
        depth    = 0 where not flooded or nodata

    Returns
    -------
    flood_depth : np.ndarray (float32), depth in metres, 0 where dry
    flood_mask  : np.ndarray (uint8), 1=flooded, 0=dry, 255=nodata
    """
    print(f"  Delineating flood for WSE rise = {wse_rise:.2f} m ...")

    valid = ~np.isnan(hand)
    flooded = valid & (hand < wse_rise)

    flood_depth = np.zeros_like(hand)
    flood_depth[flooded] = wse_rise - hand[flooded]
    flood_depth[~valid] = NODATA

    flood_mask_arr = np.zeros(hand.shape, dtype=np.uint8)
    flood_mask_arr[flooded] = 1
    flood_mask_arr[~valid] = 255  # nodata sentinel

    n_flood = int(flooded.sum())
    n_total = int(valid.sum())
    pct = 100.0 * n_flood / max(n_total, 1)
    print(f"    Flooded pixels : {n_flood:,}  ({pct:.1f}% of valid area)")
    print(f"    Max flood depth: {float(flood_depth[flooded].max() if n_flood else 0):.2f} m")
    return flood_depth.astype(np.float32), flood_mask_arr


# =============================================================================
# STEP 6 – SAVE OUTPUTS
# =============================================================================

def save_raster(array: np.ndarray, profile: dict,
                out_path: str, dtype=None, nodata=None) -> None:
    """Write a numpy array as a GeoTIFF with LZW compression."""
    out_profile = profile.copy()
    out_profile.update({
        "driver":    "GTiff",
        "count":     1,
        "compress":  "lzw",
        "dtype":     dtype or str(array.dtype),
        "nodata":    nodata,
        "tiled":     True,
        "blockxsize": 256,
        "blockysize": 256,
    })
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(array, 1)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"    Saved -> {out_path}  ({size_kb:.1f} KB)")


# =============================================================================
# STEP 7 – VISUALIZATION
# =============================================================================

def visualize(dem: np.ndarray, flood_depth: np.ndarray,
              flood_mask_arr: np.ndarray, profile: dict,
              wse_rise: float, q_peak: float, out_path: str) -> None:
    """
    Produce a publication-quality flood map:
      - Hillshaded DEM as basemap
      - Flood depth overlaid in blue colour ramp
      - River network highlighted in cyan
    """
    print(f"  Generating flood map -> {out_path}")

    # --- Hillshade from DEM ---
    # Simple analytical hillshade using gradient
    dy, dx = np.gradient(np.where(np.isnan(dem), 0, dem))
    azimuth_rad = np.deg2rad(315)
    altitude_rad = np.deg2rad(45)
    hillshade = (np.cos(altitude_rad) * (-dx * np.cos(azimuth_rad)
                 - dy * np.sin(azimuth_rad))
                 + np.sin(altitude_rad))
    hillshade = np.clip(hillshade, 0, 1)

    # Mask nodata
    valid_mask = ~np.isnan(dem)
    dem_display = np.where(valid_mask, dem, np.nan)
    flood_display = np.where(flood_mask_arr == 1, flood_depth, np.nan)
    has_flood = not np.all(np.isnan(flood_display))

    fig, ax = plt.subplots(figsize=(13, 9))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")

    # Compute geographic extent for axes labels
    tf = profile["transform"]
    extent = [
        tf.c,                                     # left
        tf.c + tf.a * profile["width"],           # right
        tf.f + tf.e * profile["height"],          # bottom
        tf.f,                                     # top
    ]

    # 1. Hillshade basemap
    ax.imshow(hillshade, extent=extent, cmap="gray",
              vmin=0.2, vmax=1.0, origin="upper", alpha=0.7)

    # 2. DEM colour ramp (terrain)
    dem_im = ax.imshow(dem_display, extent=extent, cmap="terrain",
                       origin="upper", alpha=0.5,
                       vmin=np.nanpercentile(dem_display, 2),
                       vmax=np.nanpercentile(dem_display, 98))

    # 3. Flood depth overlay
    if has_flood:
        flood_cmap = plt.cm.Blues.copy()
        flood_cmap.set_bad(alpha=0)           # transparent for NaN
        flood_cmap.set_under(alpha=0)         # transparent for 0
        flood_im = ax.imshow(
            np.where(np.isnan(flood_display), -1, flood_display),
            extent=extent, cmap=flood_cmap,
            vmin=0.01,
            vmax=np.nanpercentile(flood_display[flood_mask_arr == 1], 95)
                 if (flood_mask_arr == 1).any() else wse_rise,
            origin="upper", alpha=0.75
        )
        cb_flood = plt.colorbar(flood_im, ax=ax, shrink=0.4,
                                pad=0.01, location="right")
        cb_flood.set_label("Flood Depth (m)", color="white", fontsize=10)
        cb_flood.ax.yaxis.set_tick_params(color="white")
        plt.setp(cb_flood.ax.yaxis.get_ticklabels(), color="white")

    # 4. DEM colorbar
    cb_dem = plt.colorbar(dem_im, ax=ax, shrink=0.4, pad=0.06, location="right")
    cb_dem.set_label("Elevation (m)", color="white", fontsize=10)
    cb_dem.ax.yaxis.set_tick_params(color="white")
    plt.setp(cb_dem.ax.yaxis.get_ticklabels(), color="white")

    # Legend
    legend_patches = [
        Patch(facecolor="#1e40af", alpha=0.8, label="Flooded area"),
        Patch(facecolor="#047857", alpha=0.6, label="Terrain (HAND model)"),
    ]
    ax.legend(handles=legend_patches, loc="lower left",
              facecolor="#1e293b", edgecolor="#334155",
              labelcolor="white", fontsize=9)

    # Labels and title
    ax.set_xlabel("Longitude", color="white", fontsize=10)
    ax.set_ylabel("Latitude",  color="white", fontsize=10)
    ax.tick_params(colors="white")
    ax.spines[:].set_color("#334155")

    flood_pct = 100 * (flood_mask_arr == 1).sum() / max(valid_mask.sum(), 1)
    ax.set_title(
        f"HAND-based Flood Inundation  |  Kopili River Basin, Assam\n"
        f"Peak Q = {q_peak:.0f} m3/s  |  WSE rise = {wse_rise:.2f} m  "
        f"|  Flooded area = {flood_pct:.1f}% of basin",
        color="white", fontsize=11, fontweight="bold", pad=12
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"    [SAVED] -> {out_path}")
    if matplotlib.get_backend().lower() != "agg":
        plt.show()


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def main():
    print("\n" + "=" * 62)
    print("  HAND Flood Inundation Model  |  Kopili River Basin")
    print("=" * 62)

    # ---- STEP 1: Load inputs ----
    print("\n--- STEP 1: Loading Input Data ---")
    dem, profile, nodata_val = load_dem(DEM_PATH)
    rivers = load_rivers(RIVERS_PATH, CRS.from_epsg(4326))
    q_peak = get_peak_discharge(DISCHARGE_CSV)

    # ---- STEP 2: Rasterize rivers -> drainage mask ----
    print("\n--- STEP 2: Rasterizing River Network ---")
    drainage_mask = rasterize_rivers(rivers, profile)

    # If river rasterization yields no pixels (e.g. CRS mismatch),
    # fall back to using lowest-elevation pixels as drainage proxy
    if drainage_mask.sum() == 0:
        print("  [WARN] River rasterization produced 0 pixels.")
        print("         Falling back: using lowest 1% elevation pixels as drainage.")
        threshold = np.nanpercentile(dem, 1)
        drainage_mask = (dem <= threshold) & ~np.isnan(dem)
        print(f"         Fallback drainage pixels: {drainage_mask.sum():,}")

    # ---- STEP 3: Compute HAND ----
    print("\n--- STEP 3: Computing HAND Raster ---")
    hand = compute_hand(dem, drainage_mask)

    # ---- STEP 4: Manning's WSE rise ----
    print("\n--- STEP 4: Estimating Water Surface Elevation (Manning's) ---")
    wse_rise = compute_wse_rise(dem, drainage_mask, q_peak, profile)

    # ---- STEP 5: Flood delineation ----
    print("\n--- STEP 5: Flood Delineation ---")
    flood_depth, flood_mask_arr = delineate_flood(hand, wse_rise)

    # ---- STEP 6: Save outputs ----
    print("\n--- STEP 6: Saving Output Rasters ---")
    save_raster(flood_depth, profile, OUT_DEPTH,
                dtype="float32", nodata=NODATA)
    save_raster(flood_mask_arr, profile, OUT_MASK,
                dtype="uint8", nodata=255)

    # ---- STEP 7: Visualize ----
    print("\n--- STEP 7: Visualizing ---")
    visualize(dem, flood_depth, flood_mask_arr, profile, wse_rise, q_peak, OUT_PLOT)

    # ---- Summary ----
    print("\n" + "=" * 62)
    print("  SUMMARY")
    print("=" * 62)
    print(f"  Peak discharge       : {q_peak:.1f} m3/s")
    print(f"  WSE rise (Manning's) : {wse_rise:.2f} m")
    valid_pixels = int((~np.isnan(dem)).sum())
    flood_pixels = int((flood_mask_arr == 1).sum())
    pixel_area_km2 = (abs(profile["transform"].a) * 111.32) \
                   * (abs(profile["transform"].e) * 111.32)
    flooded_area_km2 = flood_pixels * pixel_area_km2
    print(f"  Flooded pixels       : {flood_pixels:,} / {valid_pixels:,}")
    print(f"  Flooded area         : ~{flooded_area_km2:.1f} km2")
    print(f"  Max flood depth      : {float(np.nanmax(flood_depth[flood_mask_arr==1])) if flood_pixels else 0:.2f} m")
    print(f"\n  Outputs:")
    print(f"    {OUT_DEPTH}   – continuous flood depth raster")
    print(f"    {OUT_MASK}    – binary inundation mask")
    print(f"    {OUT_PLOT}    – flood map figure")
    print("=" * 62)


if __name__ == "__main__":
    main()
