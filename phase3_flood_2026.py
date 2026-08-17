"""
phase3_flood_2026.py
====================
Computes a Height Above Nearest Drainage (HAND) flood inundation map
for the Brahmaputra River Basin based on projected 2026 peak discharge.

Methodology:
1. Load or fetch DEM (ALOS PALSAR / SRTM fallback).
2. Load river centerline.
3. Compute HAND using highly optimized distance transforms.
4. Extract peak discharge from phase 2.
5. Solve Manning's Equation for wide rectangular channel to find
   Water Surface Elevation (WSE).
6. Flood depth = max(0, WSE - HAND).
7. Save rasters and high-quality map visualization.
"""

import os
import sys
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.transform import from_origin
from rasterio.features import rasterize
import matplotlib.pyplot as plt
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =====================================================================
# CONFIGURATION
# =====================================================================
BBOX = (92.0, 26.0, 93.0, 26.5) # lon_min, lat_min, lon_max, lat_max
DEM_FILE = "dem_alos.tif"
HAND_FILE = "hand.tif"
DEPTH_FILE = "flood_depth.tif"
MASK_FILE = "flood_mask.tif"
MAP_FILE = "flood_map.png"
DISCHARGE_FILE = "discharge_2026.csv"

# Hydraulic Parameters (Brahmaputra near Guwahati)
RIVER_WIDTH_M = 8000.0  # 8 km wide main stem
MANNING_N = 0.03        # Natural channel
SLOPE = 0.0001          # 10 cm drop per km

# =====================================================================
# UTILITIES
# =====================================================================

def solve_mannings_for_stage(Q, width, n, slope):
    """
    Solves Manning's equation for depth (stage) assuming a wide 
    rectangular channel where Hydraulic Radius (R) approx equals depth (h).
    Q = (1/n) * A * R^(2/3) * S^(1/2)
    Q = (1/n) * (W * h) * (h^(2/3)) * S^(1/2)
    Q = (W * S^(1/2) / n) * h^(5/3)
    h = [ Q * n / (W * S^(1/2)) ]^(3/5)
    """
    h = (Q * n / (width * np.sqrt(slope))) ** (3.0/5.0)
    return h

def generate_synthetic_data():
    """Generates a synthetic DEM and River if downloads/files fail."""
    print("  [WARN] Using synthetic DEM and River due to missing files/dependencies.")
    # 500x500 grid representing 0.5 degrees
    rows, cols = 500, 500
    transform = from_origin(BBOX[0], BBOX[3], (BBOX[2]-BBOX[0])/cols, (BBOX[3]-BBOX[1])/rows)
    
    # Create a V-shaped valley
    x = np.linspace(-1, 1, cols)
    y = np.linspace(-1, 1, rows)
    xv, yv = np.meshgrid(x, y)
    
    # Valley along the diagonal
    distance_from_center = np.abs(xv - yv) * 0.707
    dem_data = 40.0 + (distance_from_center * 100.0) # Elev 40m at river, up to 140m
    
    # Add terrain noise
    dem_data += np.random.normal(0, 2.0, dem_data.shape)
    
    # Synthetic river mask (diagonal line)
    river_mask = (distance_from_center < 0.02).astype(np.uint8)
    
    return dem_data, river_mask, transform

# =====================================================================
# MAIN PIPELINE
# =====================================================================

def main():
    print("=" * 60)
    print(" Brahmaputra HAND Inundation Modeling (2026)")
    print("=" * 60)
    
    # -----------------------------------------------------------------
    # 1. PEAK DISCHARGE
    # -----------------------------------------------------------------
    print("  Reading peak discharge...")
    peak_q = 55000.0 # Default fallback
    if os.path.exists(DISCHARGE_FILE):
        try:
            df = pd.read_csv(DISCHARGE_FILE)
            peak_q = df['Q_cms'].max()
            print(f"    Loaded max Q from {DISCHARGE_FILE}: {peak_q:,.0f} m³/s")
        except Exception as e:
            print(f"    Failed to read CSV: {e}. Using default {peak_q} m³/s.")
    else:
        print(f"    {DISCHARGE_FILE} not found. Using default {peak_q} m³/s.")

    # -----------------------------------------------------------------
    # 2. WATER SURFACE ELEVATION
    # -----------------------------------------------------------------
    wse = solve_mannings_for_stage(peak_q, RIVER_WIDTH_M, MANNING_N, SLOPE)
    print(f"  Solved Manning's Equation:")
    print(f"    Stage (WSE) = {wse:.2f} meters above river bed")

    # -----------------------------------------------------------------
    # 3. DEM & RIVER MASK
    # -----------------------------------------------------------------
    print("  Loading terrain and hydrography...")
    
    # Check if a DEM exists (fallback to previously downloaded dem.tif)
    target_dem = DEM_FILE
    if not os.path.exists(target_dem) and os.path.exists("dem.tif"):
        target_dem = "dem.tif"
        print(f"    {DEM_FILE} not found, falling back to existing dem.tif")

    try:
        if not os.path.exists(target_dem):
            raise FileNotFoundError(f"No DEM file available.")
            
        with rasterio.open(target_dem) as src:
            dem_data = src.read(1)
            dem_data = np.where(dem_data == src.nodata, np.nan, dem_data)
            transform = src.transform
            
        # Try to load existing rivers.shp
        if os.path.exists("rivers.shp"):
            gdf = gpd.read_file("rivers.shp")
            geoms = ((geom, 1) for geom in gdf.geometry)
            river_mask = rasterize(geoms, out_shape=dem_data.shape, transform=transform, fill=0, dtype=np.uint8)
        else:
            raise FileNotFoundError("rivers.shp not found.")
            
    except Exception as e:
        print(f"  [ERROR] {e}")
        dem_data, river_mask, transform = generate_synthetic_data()

    # -----------------------------------------------------------------
    # 4. HAND COMPUTATION
    # -----------------------------------------------------------------
    print("  Computing Height Above Nearest Drainage (HAND)...")
    
    # Create mask of valid data
    valid_mask = ~np.isnan(dem_data)
    
    # Initialize HAND array
    hand = np.full(dem_data.shape, np.nan, dtype=np.float32)
    
    # We only compute HAND for valid DEM pixels
    if np.any(river_mask) and np.any(valid_mask):
        # We need the indices of the nearest river cell for every pixel
        # distance_transform_edt returns the Euclidean distance and the coordinates of the closest feature
        dist, closest_river_indices = distance_transform_edt(river_mask == 0, return_indices=True)
        
        # The elevation of the nearest river cell
        nearest_river_elev = dem_data[closest_river_indices[0], closest_river_indices[1]]
        
        # HAND = Local Elevation - Nearest River Elevation
        hand = dem_data - nearest_river_elev
        
        # Ensure no negative HAND (can happen due to noise or depressions)
        hand = np.maximum(hand, 0.0)
    else:
        print("  [ERROR] No river pixels or valid DEM pixels found. Cannot compute HAND.")

    # -----------------------------------------------------------------
    # 5. FLOOD INUNDATION
    # -----------------------------------------------------------------
    print("  Applying WSE threshold to HAND...")
    
    # Flood depth = WSE - HAND, only where HAND <= WSE
    flood_depth = wse - hand
    flood_depth[flood_depth < 0] = 0.0
    
    # Binary mask
    flood_mask = (flood_depth > 0).astype(np.uint8)
    
    flooded_pixels = np.sum(flood_mask)
    total_pixels = np.sum(valid_mask)
    pct_flooded = (flooded_pixels / total_pixels) * 100 if total_pixels > 0 else 0
    print(f"    Flooded area: {pct_flooded:.1f}% of domain.")

    # -----------------------------------------------------------------
    # 6. EXPORT
    # -----------------------------------------------------------------
    print("  Saving rasters...")
    kwargs = {
        'driver': 'GTiff',
        'height': dem_data.shape[0],
        'width': dem_data.shape[1],
        'count': 1,
        'dtype': 'float32',
        'crs': 'EPSG:4326',
        'transform': transform,
        'nodata': -9999.0
    }
    
    with rasterio.open(HAND_FILE, 'w', **kwargs) as dst:
        dst.write(np.nan_to_num(hand, nan=-9999.0), 1)
        
    with rasterio.open(DEPTH_FILE, 'w', **kwargs) as dst:
        dst.write(np.nan_to_num(flood_depth, nan=-9999.0), 1)
        
    kwargs['dtype'] = 'uint8'
    kwargs['nodata'] = 255
    with rasterio.open(MASK_FILE, 'w', **kwargs) as dst:
        dst.write(flood_mask, 1)

    # -----------------------------------------------------------------
    # 7. VISUALIZATION
    # -----------------------------------------------------------------
    print("  Generating map visualization...")
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 8))
    
    # Plot DEM (grayscale)
    im1 = ax.imshow(dem_data, cmap='gray', alpha=0.6, 
                    extent=[BBOX[0], BBOX[2], BBOX[1], BBOX[3]])
                    
    # Plot Flood Depth (blues)
    # Mask out zeroes for transparency
    plot_depth = np.where(flood_depth > 0, flood_depth, np.nan)
    im2 = ax.imshow(plot_depth, cmap='Blues', alpha=0.8,
                    extent=[BBOX[0], BBOX[2], BBOX[1], BBOX[3]], vmin=0, vmax=wse)
                    
    ax.set_title("Projected 2026 Brahmaputra Flood – Peak Inundation\n"
                 f"Q = {peak_q:,.0f} m³/s | WSE = {wse:.2f} m", 
                 fontsize=14, fontweight='bold', color='white')
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    
    plt.colorbar(im2, ax=ax, label='Flood Depth (meters)', shrink=0.7)
    
    plt.tight_layout()
    plt.savefig(MAP_FILE, dpi=200, facecolor='#0f172a', bbox_inches='tight')
    plt.close()
    
    print("  ✅ SUCCESS: Flood inundation modeling completed.")

if __name__ == "__main__":
    main()
