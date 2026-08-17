"""
download_dem_rivers.py
======================
Downloads SRTM 30 m elevation data and the HydroSHEDS river-network
shapefile for a user-defined bounding box, then:
  - Clips the river network to the bounding box
  - Resamples the DEM from 30 m → 90 m for faster processing
  - Saves results as:
      dem.tif     – resampled 90 m elevation raster
      rivers.shp  – clipped river network

Study area: Kopili River, Assam, India
Bounding box: lat 25.5–26.5, lon 92.0–93.5

Dependencies (install with pip):
    pip install pystac-client planetary-computer rasterio geopandas
                fiona shapely requests tqdm numpy
"""

import os
import sys

# Force UTF-8 output on Windows consoles (avoids cp1252 UnicodeEncodeError)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import zipfile
import tempfile
import warnings
import math

import requests
import numpy as np
from tqdm import tqdm

import rasterio
from rasterio.merge import merge
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from rasterio.mask import mask as rasterio_mask

import geopandas as gpd
from shapely.geometry import box

import pystac_client
import planetary_computer

warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# 1.  CONFIGURATION
# ---------------------------------------------------------------------------

# Bounding box for the Kopili River basin, Assam
# (min_lon, min_lat, max_lon, max_lat)  ← standard GeoJSON / STAC order
BBOX = (92.0, 25.5, 93.5, 26.5)

# Output paths
OUT_DEM = "dem.tif"          # resampled 90 m DEM
OUT_RIVERS = "rivers.shp"   # clipped river network

# Target resolution after resampling (degrees ≈ 90 m at equator)
TARGET_RES_DEG = 90 / 111_320  # ~0.000809 degrees

# HydroSHEDS – level-1 river network (global 15-arcsec)
# Using HydroSHEDS v1 from the HydroSHEDS data portal (free, public)
HYDROSHEDS_URL = (
    "https://data.hydrosheds.org/file/HydroRIVERS/"
    "HydroRIVERS_v10_as_shp.zip"  # Asia continent shapefile (~120 MB)
)
HYDROSHEDS_ZIP = "HydroRIVERS_v10_as_shp.zip"
HYDROSHEDS_DIR = "hydrosheds_data"

# Planetary Computer STAC endpoint
PC_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
SRTM_COLLECTION = "cop-dem-glo-30"   # Copernicus DEM 30 m (based on SRTM)


# ---------------------------------------------------------------------------
# 2.  HELPER UTILITIES
# ---------------------------------------------------------------------------

def download_file(url: str, dest_path: str, desc: str = "") -> None:
    """Stream-download a file with a progress bar."""
    print(f"\n[DOWNLOAD] {desc or url}")
    print(f"  → {dest_path}")
    try:
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    total = int(response.headers.get("content-length", 0))
    with open(dest_path, "wb") as fh, tqdm(
        total=total, unit="B", unit_scale=True, desc=desc, ncols=80
    ) as pbar:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            fh.write(chunk)
            pbar.update(len(chunk))
    print("  ✓ Download complete.")


def bbox_to_shapely(bbox):
    """Convert (min_lon, min_lat, max_lon, max_lat) to a Shapely polygon."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return box(min_lon, min_lat, max_lon, max_lat)


# ---------------------------------------------------------------------------
# 3.  STEP A – Download & mosaic Copernicus/SRTM DEM tiles via Planetary Computer
# ---------------------------------------------------------------------------

def download_dem(bbox: tuple, out_path: str, target_res: float) -> None:
    """
    Query Planetary Computer STAC for Copernicus GLO-30 DEM tiles,
    download them, mosaic, clip to bbox, resample to ~90 m, and save.
    """
    print("\n" + "=" * 60)
    print("STEP A: Downloading DEM (Copernicus GLO-30 / SRTM 30 m)")
    print("=" * 60)

    min_lon, min_lat, max_lon, max_lat = bbox

    # --- A1. Search STAC catalog ---
    print("\n[STAC] Connecting to Planetary Computer …")
    try:
        catalog = pystac_client.Client.open(
            PC_STAC_API,
            modifier=planetary_computer.sign_inplace,
        )
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to Planetary Computer STAC: {exc}") from exc

    search = catalog.search(
        collections=[SRTM_COLLECTION],
        bbox=list(bbox),
    )
    items = list(search.items())

    if not items:
        raise RuntimeError(
            f"No DEM tiles found for bbox {bbox} in collection '{SRTM_COLLECTION}'."
        )
    print(f"  Found {len(items)} DEM tile(s).")

    # --- A2. Download individual tiles into a temp directory ---
    tile_paths = []
    with tempfile.TemporaryDirectory(prefix="dem_tiles_") as tmpdir:
        for idx, item in enumerate(items, 1):
            # The data asset is named 'data' in this collection
            asset = item.assets.get("data")
            if asset is None:
                print(f"  [WARN] Item {item.id} has no 'data' asset — skipping.")
                continue

            tile_url = asset.href
            tile_path = os.path.join(tmpdir, f"tile_{idx:03d}.tif")
            print(f"\n  Tile {idx}/{len(items)}: {item.id}")
            try:
                download_file(tile_url, tile_path, desc=f"Tile {idx}")
                tile_paths.append(tile_path)
            except RuntimeError as exc:
                print(f"  [WARN] Skipping tile {item.id}: {exc}")

        if not tile_paths:
            raise RuntimeError("No DEM tiles could be downloaded.")

        # --- A3. Mosaic tiles ---
        print(f"\n[MOSAIC] Merging {len(tile_paths)} tile(s) …")
        src_files = [rasterio.open(p) for p in tile_paths]
        mosaic, mosaic_transform = merge(src_files)
        mosaic_crs = src_files[0].crs
        for src in src_files:
            src.close()

        # --- A4. Clip to bounding box ---
        print("[CLIP]  Clipping mosaic to bounding box …")
        clip_geom = [bbox_to_shapely(bbox).__geo_interface__]

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_mosaic:
            tmp_mosaic_path = tmp_mosaic.name

        # Write mosaic to a temp file so rasterio.mask can work on it
        with rasterio.open(
            tmp_mosaic_path,
            "w",
            driver="GTiff",
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            count=1,
            dtype=mosaic.dtype,
            crs=mosaic_crs,
            transform=mosaic_transform,
        ) as tmp_ds:
            tmp_ds.write(mosaic[0], 1)

        with rasterio.open(tmp_mosaic_path) as tmp_ds:
            clipped, clipped_transform = rasterio_mask(
                tmp_ds, clip_geom, crop=True, filled=True, nodata=-9999
            )
            clipped_meta = tmp_ds.meta.copy()

        os.unlink(tmp_mosaic_path)

        # --- A5. Resample to target resolution (~90 m) ---
        src_res_x = abs(clipped_transform.a)   # pixel width in degrees
        scale_factor = src_res_x / target_res  # will be < 1 (downsampling)

        new_height = max(1, math.ceil(clipped.shape[1] * scale_factor))
        new_width  = max(1, math.ceil(clipped.shape[2] * scale_factor))

        print(
            f"[RESAMPLE] {clipped.shape[1]}×{clipped.shape[2]} → "
            f"{new_height}×{new_width} pixels (scale factor ≈ {scale_factor:.3f})"
        )

        # Build new affine transform for resampled grid
        new_transform = from_bounds(
            min_lon, min_lat, max_lon, max_lat, new_width, new_height
        )

        resampled = np.empty(
            (1, new_height, new_width), dtype=clipped.dtype
        )

        # Write clipped to a temp file, then resample on read
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_clip:
            tmp_clip_path = tmp_clip.name

        clipped_meta.update(
            {
                "height": clipped.shape[1],
                "width": clipped.shape[2],
                "transform": clipped_transform,
                "nodata": -9999,
            }
        )
        with rasterio.open(tmp_clip_path, "w", **clipped_meta) as ds:
            ds.write(clipped[0], 1)

        with rasterio.open(tmp_clip_path) as ds:
            resampled_data = ds.read(
                1,
                out_shape=(new_height, new_width),
                resampling=Resampling.bilinear,
            )
        os.unlink(tmp_clip_path)

        # --- A6. Save final DEM ---
        print(f"[SAVE]  Writing DEM → {out_path}")
        out_meta = clipped_meta.copy()
        out_meta.update(
            {
                "driver": "GTiff",
                "height": new_height,
                "width": new_width,
                "transform": new_transform,
                "crs": CRS.from_epsg(4326),
                "nodata": -9999,
                "compress": "lzw",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
            }
        )
        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(resampled_data, 1)

    print(f"  ✓ DEM saved: {out_path}  ({new_height}×{new_width} px, ~90 m res)")


# ---------------------------------------------------------------------------
# 4.  STEP B – Download & clip HydroSHEDS river network
# ---------------------------------------------------------------------------

def download_rivers(bbox: tuple, out_path: str) -> None:
    """
    Download the HydroRIVERS Asia shapefile (if not already cached),
    clip to the bounding box, and save as a new shapefile.
    """
    print("\n" + "=" * 60)
    print("STEP B: Downloading HydroSHEDS River Network")
    print("=" * 60)

    # --- B1. Download zip if needed ---
    os.makedirs(HYDROSHEDS_DIR, exist_ok=True)
    zip_path = os.path.join(HYDROSHEDS_DIR, HYDROSHEDS_ZIP)

    if os.path.exists(zip_path):
        print(f"  [CACHE] Found existing zip: {zip_path}")
    else:
        download_file(HYDROSHEDS_URL, zip_path, desc="HydroRIVERS Asia")

    # --- B2. Extract zip ---
    print(f"\n[EXTRACT] Unzipping {zip_path} …")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(HYDROSHEDS_DIR)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Downloaded zip appears corrupt: {exc}\n"
            f"Delete {zip_path} and re-run."
        ) from exc
    print("  ✓ Extraction complete.")

    # --- B3. Locate the .shp file ---
    shp_file = None
    for root, dirs, files in os.walk(HYDROSHEDS_DIR):
        for fname in files:
            if fname.endswith(".shp") and "HydroRIVERS" in fname:
                shp_file = os.path.join(root, fname)
                break
        if shp_file:
            break

    if shp_file is None:
        raise RuntimeError(
            "Could not find HydroRIVERS .shp file inside the downloaded zip."
        )
    print(f"  Found shapefile: {shp_file}")

    # --- B4. Read and clip using the bounding box ---
    print(f"\n[CLIP]  Reading & clipping river network to bbox {bbox} …")
    try:
        rivers = gpd.read_file(shp_file, bbox=bbox)  # bbox filter on read (fast)
    except Exception as exc:
        raise RuntimeError(f"Failed to read shapefile: {exc}") from exc

    if rivers.empty:
        print("  [WARN] No river features found within the bounding box!")
        print("         Saving an empty shapefile for consistency.")
    else:
        print(f"  Found {len(rivers)} river segment(s) within bbox.")
        # Clip geometries precisely to the bbox polygon
        bbox_poly = bbox_to_shapely(bbox)
        rivers = gpd.clip(rivers, bbox_poly)
        print(f"  After precise clip: {len(rivers)} segment(s).")

    # Ensure CRS is set (HydroSHEDS ships as WGS 84)
    if rivers.crs is None:
        rivers = rivers.set_crs(epsg=4326)

    # --- B5. Save ---
    print(f"[SAVE]  Writing rivers → {out_path}")
    rivers.to_file(out_path)
    print(f"  ✓ River network saved: {out_path}")


# ---------------------------------------------------------------------------
# 5.  MAIN
# ---------------------------------------------------------------------------

def main():
    print("\n" + "=" * 62)
    print("  Kopili River Basin - DEM & Hydrology Download Script")
    print("=" * 62)
    print(f"\nBounding box : lon {BBOX[0]}-{BBOX[2]}, lat {BBOX[1]}-{BBOX[3]}")
    print(f"DEM output   : {OUT_DEM}")
    print(f"River output : {OUT_RIVERS}")

    errors = []

    # --- Download DEM ---
    if os.path.exists(OUT_DEM):
        print(f"\n[DEM] File already exists: {OUT_DEM} — skipping download.")
    else:
        try:
            download_dem(BBOX, OUT_DEM, TARGET_RES_DEG)
        except Exception as exc:
            errors.append(f"DEM download failed: {exc}")
            print(f"\n[ERROR] {exc}")

    # --- Download river network ---
    if os.path.exists(OUT_RIVERS):
        print(f"\n[RIVERS] File already exists: {OUT_RIVERS} — skipping download.")
    else:
        try:
            download_rivers(BBOX, OUT_RIVERS)
        except Exception as exc:
            errors.append(f"River download failed: {exc}")
            print(f"\n[ERROR] {exc}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if os.path.exists(OUT_DEM):
        size_mb = os.path.getsize(OUT_DEM) / (1024 ** 2)
        print(f"  ✓ {OUT_DEM}  ({size_mb:.1f} MB)")
    else:
        print(f"  ✗ {OUT_DEM}  — NOT CREATED")

    if os.path.exists(OUT_RIVERS):
        size_mb = os.path.getsize(OUT_RIVERS) / (1024 ** 2)
        print(f"  ✓ {OUT_RIVERS}  ({size_mb:.2f} MB)")
    else:
        print(f"  ✗ {OUT_RIVERS}  — NOT CREATED")

    if errors:
        print("\nErrors encountered:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("\nAll done! ✓")


if __name__ == "__main__":
    main()
