"""
flood_routing.py
================
Flood-Aware Evacuation Route Planner — Kopili Basin, Assam
-----------------------------------------------------------
Finds the safest dry evacuation route from a village to the nearest
flood shelter, avoiding roads where flood water depth >= 0.3 m.

Pipeline:
  1. Fetch road network from OpenStreetMap via osmnx
     (fallback: synthetic grid if OSM fetch fails / offline)
  2. Read flood_depth.tif — rasterize flood depth onto road edges
     by sampling multiple points along each edge geometry
  3. Build a weighted NetworkX graph:
        weight = edge_length_m   if max_depth_on_edge < DEPTH_THRESHOLD
        weight = inf             if road is flooded / impassable
  4. Run Dijkstra's shortest-path (networkx.shortest_path)
  5. If no dry path → greedy fallback (minimum-flood path)
  6. Output:
        evacuation_route.geojson  — GeoJSON LineString of route
        route_map.png             — matplotlib visualization
  7. Print turn-by-turn instructions to console

Coordinates (Kopili Basin, Assam):
  Village start  : Hojai town area  (26.00, 92.85) lat/lon
  Flood shelter  : Lanka block HQ   (26.13, 92.78) lat/lon

Dependencies:
  pip install osmnx networkx geopandas rasterio shapely
              numpy matplotlib scipy requests

References:
  Dijkstra (1959). A note on two problems in connexion with graphs.
  Boeing (2017). OSMnx: New methods for acquiring & analysing street networks.
"""

import os
import sys
import json
import math
import warnings

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
import rasterio
import rasterio.transform
import matplotlib
if "--headless" in sys.argv:
    matplotlib.use("Agg")
else:
    try:
        matplotlib.use("TkAgg")
    except Exception:
        matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
from matplotlib.lines import Line2D
from shapely.geometry import (
    Point, LineString, MultiLineString, box as shapely_box
)
from shapely.ops import split, unary_union

warnings.filterwarnings("ignore")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# CONFIGURATION
# =============================================================================

FLOOD_RASTER     = "flood_depth.tif"
OUT_GEOJSON      = "evacuation_route.geojson"
OUT_PLOT         = "route_map.png"
OUT_REPORT       = "evacuation_report.txt"

# Impassable depth threshold (m)
# Roads with ANY sampled point at or above this are marked flooded
DEPTH_THRESHOLD  = 0.3        # 30 cm — standard for road impassability

# Number of sample points per road edge for depth interrogation
# More = more accurate flood classification, but slower
SAMPLES_PER_EDGE = 10

# Study area bounding box (lon_min, lat_min, lon_max, lat_max)
BBOX             = (92.5, 25.7, 93.2, 26.3)

# Evacuation origin and destination
VILLAGE_LATLON   = (26.00, 92.85)   # Hojai area village (lat, lon)
SHELTER_LATLON   = (26.13, 92.78)   # Lanka block flood shelter (lat, lon)

# OSM road types to include (ordered by preference)
OSM_ROAD_FILTER  = (
    '["highway"~"primary|secondary|tertiary|'
    'unclassified|residential|track|road"]'
)

# Fallback synthetic grid (used if OSM fetch fails)
GRID_SPACING_DEG = 0.015   # ~1.5 km grid squares


# =============================================================================
# STEP 1  –  FETCH ROAD NETWORK
# =============================================================================

def fetch_osm_network(bbox: tuple) -> nx.MultiDiGraph:
    """
    Download road network from OpenStreetMap using osmnx.

    osmnx returns a MultiDiGraph where:
      - nodes = road intersections (lon/lat attributes)
      - edges = road segments (geometry, length, name, highway attributes)

    Returns an undirected copy (MultiGraph) for routing in both directions.
    """
    try:
        import osmnx as ox
        ox.settings.log_console = False
        ox.settings.use_cache   = True
        ox.settings.requests_timeout = 60
        ox.settings.timeout = 60
        print("  Fetching road network from OpenStreetMap ...")
        min_lon, min_lat, max_lon, max_lat = bbox
        G = ox.graph_from_bbox(
            bbox=(min_lon, min_lat, max_lon, max_lat),
            network_type="drive",
            custom_filter=OSM_ROAD_FILTER,
        )
        G_undirected = ox.convert.to_undirected(G)
        n_nodes = G_undirected.number_of_nodes()
        n_edges = G_undirected.number_of_edges()
        print(f"  OSM network: {n_nodes:,} nodes, {n_edges:,} edges")
        return G_undirected
    except Exception as exc:
        print(f"  [WARN] OSM fetch failed: {exc}")
        print("  Falling back to synthetic road grid ...")
        return None


def build_synthetic_grid(bbox: tuple, spacing: float) -> nx.Graph:
    """
    Build a regular rectangular road grid as fallback.

    Nodes are grid intersections; edges are horizontal/vertical road segments.
    Each edge carries: geometry (LineString), length_m, name, highway.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = np.arange(min_lon, max_lon + spacing, spacing)
    lats = np.arange(min_lat, max_lat + spacing, spacing)

    G = nx.Graph()

    # Add nodes at every grid intersection
    node_id = 0
    node_map = {}
    for lon in lons:
        for lat in lats:
            G.add_node(node_id, x=lon, y=lat)
            node_map[(round(lon, 6), round(lat, 6))] = node_id
            node_id += 1

    # Add horizontal and vertical edges
    def _haversine_m(lon1, lat1, lon2, lat2):
        R = 6_371_000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return 2 * R * math.asin(math.sqrt(a))

    for lon in lons:
        for lat in lats:
            key = (round(lon, 6), round(lat, 6))
            n = node_map[key]
            # Right neighbour
            rkey = (round(lon + spacing, 6), round(lat, 6))
            if rkey in node_map:
                m = node_map[rkey]
                dist = _haversine_m(lon, lat, lon + spacing, lat)
                geom = LineString([(lon, lat), (lon + spacing, lat)])
                G.add_edge(n, m, geometry=geom, length=dist,
                           name="Grid Road", highway="unclassified")
            # Up neighbour
            ukey = (round(lon, 6), round(lat + spacing, 6))
            if ukey in node_map:
                m = node_map[ukey]
                dist = _haversine_m(lon, lat, lon, lat + spacing)
                geom = LineString([(lon, lat), (lon, lat + spacing)])
                G.add_edge(n, m, geometry=geom, length=dist,
                           name="Grid Road", highway="unclassified")

    print(f"  Synthetic grid: {G.number_of_nodes():,} nodes, "
          f"{G.number_of_edges():,} edges")
    return G


def load_network(bbox: tuple):
    """Try OSM first; fall back to synthetic grid."""
    G = fetch_osm_network(bbox)
    if G is None:
        G = build_synthetic_grid(bbox, GRID_SPACING_DEG)
        G.graph["osm"] = False
    else:
        G.graph["osm"] = True
    return G


# =============================================================================
# STEP 2  –  SAMPLE FLOOD DEPTH ALONG ROAD EDGES
# =============================================================================

def get_edge_geometry(G, u, v, data: dict) -> LineString:
    """
    Extract or reconstruct the geometry of an edge.
    osmnx edges have a 'geometry' attribute (LineString).
    Synthetic/simple edges may only have node coordinates.
    """
    geom = data.get("geometry")
    if geom is not None and not geom.is_empty:
        return geom
    # Reconstruct from node coordinates
    x_u, y_u = G.nodes[u]["x"], G.nodes[u]["y"]
    x_v, y_v = G.nodes[v]["x"], G.nodes[v]["y"]
    return LineString([(x_u, y_u), (x_v, y_v)])


def sample_depth_along_edge(geom: LineString, src: rasterio.DatasetReader,
                             n_samples: int = SAMPLES_PER_EDGE) -> float:
    """
    Interpolate n_samples evenly-spaced points along a road segment and
    query the flood depth raster at each point.

    Returns the MAXIMUM depth found along the edge.
    (Max is conservative — if any part of the road floods, it's impassable.)
    """
    length = geom.length
    if length == 0:
        return 0.0

    depths = []
    for frac in np.linspace(0, 1, n_samples):
        pt = geom.interpolate(frac, normalized=True)
        # Convert geographic coordinates to raster pixel row/col
        try:
            row, col = rasterio.transform.rowcol(src.transform, pt.x, pt.y)
            row, col = int(row), int(col)
            if 0 <= row < src.height and 0 <= col < src.width:
                val = float(src.read(1)[row, col])
                nodata = src.nodata if src.nodata is not None else -9999.0
                if val != nodata and val >= 0:
                    depths.append(val)
        except Exception:
            pass

    return float(max(depths)) if depths else 0.0


def annotate_flood_depth(G, flood_raster_path: str, n_samples: int = SAMPLES_PER_EDGE):
    """
    Read flood_depth.tif and annotate every edge with:
      - max_flood_depth  : maximum sampled depth (m)
      - is_flooded       : True if max_depth >= DEPTH_THRESHOLD
      - flood_weight     : edge_length if dry, math.inf if flooded

    Also computes edge length in metres if not already present.
    """
    print(f"  Sampling flood depth on {G.number_of_edges():,} edges ...")
    if not os.path.exists(flood_raster_path):
        print(f"  [WARN] {flood_raster_path} not found — treating all roads as dry.")
        for u, v, k, data in (G.edges(keys=True, data=True)
                               if G.is_multigraph()
                               else [(u, v, 0, d) for u, v, d in G.edges(data=True)]):
            data["max_flood_depth"] = 0.0
            data["is_flooded"] = False
            data["flood_weight"] = data.get("length", 100.0)
        return G

    with rasterio.open(flood_raster_path) as src:
        nodata = src.nodata

        edge_iter = (G.edges(keys=True, data=True) if G.is_multigraph()
                     else [(u, v, 0, d) for u, v, d in G.edges(data=True)])

        n_flooded = 0
        n_dry = 0
        for u, v, k, data in edge_iter:
            geom = get_edge_geometry(G, u, v, data)
            depth = sample_depth_along_edge(geom, src, n_samples)
            length = data.get("length", geom.length * 111_320)  # deg → approx m

            data["max_flood_depth"] = round(depth, 4)
            data["is_flooded"]      = depth >= DEPTH_THRESHOLD
            data["flood_weight"]    = math.inf if data["is_flooded"] else length

            if data["is_flooded"]:
                n_flooded += 1
            else:
                n_dry += 1

    total = n_flooded + n_dry
    pct = 100.0 * n_flooded / max(total, 1)
    print(f"  Flooded edges  : {n_flooded:,} / {total:,} ({pct:.1f}%)")
    print(f"  Passable edges : {n_dry:,} ({100-pct:.1f}%)")
    return G


# =============================================================================
# STEP 3  –  SNAP POINTS TO NEAREST GRAPH NODE
# =============================================================================

def nearest_node(G, lon: float, lat: float) -> int:
    """
    Find the graph node nearest to (lon, lat) by Euclidean distance
    in geographic coordinates (good enough for small areas).
    """
    best_node = None
    best_dist = math.inf
    for node, data in G.nodes(data=True):
        dx = data.get("x", data.get("lon", 0)) - lon
        dy = data.get("y", data.get("lat", 0)) - lat
        d  = math.sqrt(dx*dx + dy*dy)
        if d < best_dist:
            best_dist = d
            best_node = node
    return best_node, best_dist


# =============================================================================
# STEP 4  –  ROUTE FINDING (DIJKSTRA + FALLBACK)
# =============================================================================

def find_dry_route(G, origin_node: int, dest_node: int):
    """
    Attempt Dijkstra's shortest path using flood_weight as edge cost.

    Returns (path_nodes, total_length_m, is_dry)
    - path_nodes : ordered list of node IDs
    - total_length_m : route length in metres
    - is_dry     : True if fully dry, False if route uses partially flooded roads
    """
    weight_key = "flood_weight"

    # --- Try 1: Fully dry route (all weights finite) ---
    try:
        path = nx.shortest_path(G, origin_node, dest_node, weight=weight_key)
        length_m = sum(
            _get_edge_weight(G, path[i], path[i+1], weight_key)
            for i in range(len(path) - 1)
        )
        return path, length_m, True
    except nx.NetworkXNoPath:
        print("  [INFO] No fully dry route exists.")
    except nx.NodeNotFound as exc:
        raise RuntimeError(f"Node not found: {exc}") from exc

    # --- Try 2: Minimum-flood fallback ---
    # Re-weight: flooded edges get high (but finite) cost = 10x distance
    print("  [FALLBACK] Searching minimum-flood route ...")

    def fallback_weight(u, v, data):
        if G.is_multigraph():
            # Pick best parallel edge
            best = min(data.values(),
                       key=lambda d: d.get("max_flood_depth", 0))
            dep = best.get("max_flood_depth", 0)
            lng = best.get("length", 100.0)
        else:
            dep = data.get("max_flood_depth", 0)
            lng = data.get("length", 100.0)
        penalty = 10.0 if dep >= DEPTH_THRESHOLD else 1.0
        return lng * penalty

    try:
        path = nx.shortest_path(G, origin_node, dest_node, weight=fallback_weight)
        length_m = sum(
            _get_edge_weight(G, path[i], path[i+1], "length")
            for i in range(len(path) - 1)
        )
        return path, length_m, False
    except nx.NetworkXNoPath:
        return [], 0.0, False


def _get_edge_weight(G, u: int, v: int, key: str) -> float:
    """Safely retrieve edge attribute (handles MultiGraph)."""
    if G.is_multigraph():
        edges = G[u][v]
        vals = [e.get(key, math.inf) for e in edges.values()]
        return min(v for v in vals if v is not None)
    return G[u][v].get(key, math.inf)


# =============================================================================
# STEP 5  –  EXTRACT ROUTE GEOMETRY AND EDGE DATA
# =============================================================================

def extract_route_data(G, path: list) -> list[dict]:
    """
    Walk the path node-by-node and collect segment metadata.

    Returns list of dicts, one per segment:
      {from_node, to_node, geometry, length_m, depth_m, is_flooded, road_name}
    """
    segments = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i+1]
        if G.is_multigraph():
            # Among parallel edges, pick the driest one
            edge_data = min(G[u][v].values(),
                            key=lambda d: d.get("max_flood_depth", 0))
        else:
            edge_data = G[u][v]

        geom  = get_edge_geometry(G, u, v, edge_data)
        segs  = {
            "from_node"  : u,
            "to_node"    : v,
            "geometry"   : geom,
            "length_m"   : edge_data.get("length", geom.length * 111_320),
            "depth_m"    : edge_data.get("max_flood_depth", 0.0),
            "is_flooded" : edge_data.get("is_flooded", False),
            "road_name"  : edge_data.get("name", edge_data.get("highway", "unnamed")),
            "road_type"  : edge_data.get("highway", "unknown"),
        }
        segments.append(segs)
    return segments


# =============================================================================
# STEP 6  –  TURN-BY-TURN DIRECTIONS
# =============================================================================

def compute_bearing(pt_a: Point, pt_b: Point) -> float:
    """Compass bearing (0=N, 90=E, 180=S, 270=W) from pt_a to pt_b."""
    lat1 = math.radians(pt_a.y)
    lat2 = math.radians(pt_b.y)
    dlon = math.radians(pt_b.x - pt_a.x)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360


def bearing_to_cardinal(bearing: float) -> str:
    """Convert compass bearing to 8-point cardinal direction."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(bearing / 45) % 8
    return dirs[idx]


def bearing_to_turn(prev_bearing: float, next_bearing: float) -> str:
    """Convert change in bearing to a turn instruction."""
    diff = (next_bearing - prev_bearing + 360) % 360
    if diff < 22.5 or diff > 337.5:
        return "Continue straight"
    elif 22.5 <= diff < 67.5:
        return "Turn slight right"
    elif 67.5 <= diff < 112.5:
        return "Turn right"
    elif 112.5 <= diff < 157.5:
        return "Turn sharp right"
    elif 157.5 <= diff < 202.5:
        return "U-turn"
    elif 202.5 <= diff < 247.5:
        return "Turn sharp left"
    elif 247.5 <= diff < 292.5:
        return "Turn left"
    else:
        return "Turn slight left"


def generate_turn_by_turn(segments: list[dict]) -> list[str]:
    """
    Generate human-readable turn-by-turn instructions.
    Returns list of instruction strings.
    """
    instructions = []
    cum_dist = 0.0
    prev_bearing = None

    for i, seg in enumerate(segments):
        geom   = seg["geometry"]
        coords = list(geom.coords)
        if len(coords) < 2:
            continue

        pt_start = Point(coords[0])
        pt_end   = Point(coords[-1])
        bearing  = compute_bearing(pt_start, pt_end)
        dist_m   = seg["length_m"]
        cum_dist += dist_m

        road  = seg["road_name"] if seg["road_name"] else "unnamed road"
        road  = str(road)[:40]  # truncate long names
        card  = bearing_to_cardinal(bearing)

        # Turn instruction
        if i == 0:
            turn = f"Head {card}"
        elif prev_bearing is not None:
            turn = bearing_to_turn(prev_bearing, bearing)
        else:
            turn = "Continue"

        # Flood warning
        flood_warn = ""
        if seg["is_flooded"]:
            flood_warn = f"  *** CAUTION: {seg['depth_m']:.2f}m WATER on road ***"

        step_num = i + 1
        instr = (
            f"  {step_num:>3}. {turn} onto {road}\n"
            f"       ({dist_m:.0f} m, heading {card},"
            f" cumulative {cum_dist/1000:.2f} km){flood_warn}"
        )
        instructions.append(instr)
        prev_bearing = bearing

    # Final step
    instructions.append(
        f"\n  --> ARRIVE at flood shelter  "
        f"(total: {cum_dist/1000:.2f} km)"
    )
    return instructions


# =============================================================================
# STEP 7  –  EXPORT GEOJSON
# =============================================================================

def export_geojson(segments: list[dict], origin_latlon: tuple,
                   dest_latlon: tuple, is_dry: bool, out_path: str) -> None:
    """
    Save route as a GeoJSON FeatureCollection with per-segment properties.
    Each segment = one LineString feature with road attributes.
    Also adds Point features for origin and destination.
    """
    features = []

    # Route segments
    for i, seg in enumerate(segments):
        geom = seg["geometry"]
        coords = [[c[0], c[1]] for c in geom.coords]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": {
                "segment_id"   : i + 1,
                "road_name"    : str(seg.get("road_name", "")),
                "road_type"    : str(seg.get("road_type", "")),
                "length_m"     : round(seg["length_m"], 1),
                "flood_depth_m": round(seg["depth_m"], 3),
                "is_flooded"   : bool(seg["is_flooded"]),
                "status"       : "FLOODED" if seg["is_flooded"] else "PASSABLE",
                "stroke"       : "#ef4444" if seg["is_flooded"] else "#22c55e",
                "stroke-width" : 4,
            }
        })

    # Origin marker
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [origin_latlon[1], origin_latlon[0]]},
        "properties": {"name": "Village (Origin)", "marker-color": "#4ade80",
                       "marker-symbol": "village"}
    })

    # Destination marker
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [dest_latlon[1], dest_latlon[0]]},
        "properties": {"name": "Flood Shelter (Destination)",
                       "marker-color": "#38bdf8", "marker-symbol": "shelter"}
    })

    fc = {
        "type"    : "FeatureCollection",
        "name"    : "Flood Evacuation Route",
        "metadata": {
            "route_type"  : "DRY" if is_dry else "MINIMUM-FLOOD",
            "depth_threshold_m": DEPTH_THRESHOLD,
            "total_segments"   : len(segments),
            "flooded_segments" : sum(1 for s in segments if s["is_flooded"]),
            "total_length_m"   : round(sum(s["length_m"] for s in segments), 1),
        },
        "features": features
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"  [SAVED] {out_path}  ({size_kb:.1f} KB, {len(features)} features)")


# =============================================================================
# STEP 8  –  VISUALIZE
# =============================================================================

def visualize_route(G, segments: list[dict], origin: tuple,
                    dest: tuple, is_dry: bool, bbox: tuple,
                    out_path: str) -> None:
    """
    Dark-theme route map:
    - All roads coloured by flood status (dry=grey, flooded=red)
    - Evacuation route highlighted (green if dry, orange if flooded)
    - Origin (green star) and destination (blue star) markers
    """
    print(f"  Generating route map -> {out_path}")

    fig, ax = plt.subplots(figsize=(13, 10))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#1e293b")
    ax.tick_params(colors="white", labelsize=8)
    ax.spines[:].set_color("#334155")

    # --- Draw ALL road edges (background) ---
    edges_to_draw = []
    if G.is_multigraph():
        for u, v, k, data in G.edges(keys=True, data=True):
            edges_to_draw.append((u, v, data))
    else:
        for u, v, data in G.edges(data=True):
            edges_to_draw.append((u, v, data))

    n_drawn = 0
    for u, v, data in edges_to_draw:
        geom = get_edge_geometry(G, u, v, data)
        xs, ys = zip(*geom.coords)
        flooded = data.get("is_flooded", False)
        col = "#ef444444" if flooded else "#33415588"
        ax.plot(xs, ys, color=col, linewidth=0.5, solid_capstyle="round")
        n_drawn += 1

    # --- Draw evacuation route (highlighted) ---
    route_geoms = []
    for seg in segments:
        geom = seg["geometry"]
        xs, ys = zip(*geom.coords)
        col = "#f97316" if seg["is_flooded"] else "#4ade80"
        lw  = 3.0
        ax.plot(xs, ys, color=col, linewidth=lw,
                solid_capstyle="round", zorder=5)
        route_geoms.append(geom)

    # --- Markers ---
    ax.plot(origin[1], origin[0], "*", color="#4ade80", markersize=16,
            zorder=10, markeredgecolor="white", markeredgewidth=0.8)
    ax.plot(dest[1], dest[0], "*", color="#38bdf8", markersize=16,
            zorder=10, markeredgecolor="white", markeredgewidth=0.8)
    ax.annotate("Village\n(Start)", xy=(origin[1], origin[0]),
                xytext=(origin[1]+0.02, origin[0]+0.02),
                color="white", fontsize=8, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color="white", lw=0.8))
    ax.annotate("Flood Shelter\n(End)", xy=(dest[1], dest[0]),
                xytext=(dest[1]+0.02, dest[0]-0.03),
                color="white", fontsize=8, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color="white", lw=0.8))

    # --- Legend ---
    legend_items = [
        Line2D([0], [0], color="#4ade80", lw=2.5, label="Dry evacuation road"),
        Line2D([0], [0], color="#f97316", lw=2.5, label="Flooded (caution)"),
        Line2D([0], [0], color="#334155", lw=1.0, label="Other dry roads"),
        Line2D([0], [0], color="#ef4444", lw=1.0, alpha=0.5, label="Flooded roads"),
        Line2D([0], [0], marker="*", color="w", markersize=10,
               markerfacecolor="#4ade80", label="Village origin"),
        Line2D([0], [0], marker="*", color="w", markersize=10,
               markerfacecolor="#38bdf8", label="Flood shelter"),
    ]
    ax.legend(handles=legend_items, loc="lower right", facecolor="#1e293b",
              edgecolor="#334155", labelcolor="white", fontsize=8)

    # Bounding box
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_xlabel("Longitude", color="white", fontsize=10)
    ax.set_ylabel("Latitude",  color="white", fontsize=10)

    route_km = sum(s["length_m"] for s in segments) / 1000
    status   = "FULLY DRY" if is_dry else "MINIMUM-FLOOD (caution)"
    ax.set_title(
        f"Flood Evacuation Route  |  Kopili Basin, Assam\n"
        f"Route status: {status}  |  Distance: {route_km:.1f} km  |  "
        f"Depth threshold: {DEPTH_THRESHOLD}m",
        color="white", fontsize=11, fontweight="bold"
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"  [SAVED] -> {out_path}")
    if matplotlib.get_backend().lower() != "agg":
        plt.show()


# =============================================================================
# STEP 9  –  SAVE TEXT REPORT
# =============================================================================

def save_report(instructions: list[str], segments: list[dict],
                origin: tuple, dest: tuple, is_dry: bool) -> None:
    total_m   = sum(s["length_m"] for s in segments)
    n_flooded = sum(1 for s in segments if s["is_flooded"])
    avg_speed_kmh = 20.0  # rural evacuation
    eta_min   = (total_m / 1000) / avg_speed_kmh * 60

    lines = [
        "=" * 62,
        "  KOPILI BASIN FLOOD EVACUATION ROUTE REPORT",
        "  Generated by flood_routing.py",
        "=" * 62,
        f"  Origin  : {origin[0]:.4f}N, {origin[1]:.4f}E  (village)",
        f"  Dest    : {dest[0]:.4f}N, {dest[1]:.4f}E  (flood shelter)",
        f"  Status  : {'FULLY DRY ROUTE' if is_dry else '*** FLOODED SEGMENTS — USE CAUTION ***'}",
        f"  Distance: {total_m/1000:.2f} km",
        f"  ETA     : ~{eta_min:.0f} min at {avg_speed_kmh:.0f} km/h",
        f"  Segments: {len(segments)} total, {n_flooded} flooded",
        "",
        "  TURN-BY-TURN DIRECTIONS",
        "  " + "-" * 50,
    ] + instructions + [
        "",
        "  FLOODED SEGMENT DETAIL",
        "  " + "-" * 50,
    ]

    for seg in segments:
        if seg["is_flooded"]:
            lines.append(
                f"  Segment {segments.index(seg)+1}: {seg['road_name']}  "
                f"depth={seg['depth_m']:.2f}m  len={seg['length_m']:.0f}m"
            )

    lines += [
        "",
        "  SAFETY ADVICE",
        "  " + "-" * 50,
        "  - Do not drive through water deeper than 0.3m",
        "  - Watch for swept bridges and culverts",
        "  - Keep engine revs high when wading (if unavoidable)",
        "  - Signal your location to SDRF if stranded",
        "  SDRF Assam: 1070  |  NDMA: 1078",
        "=" * 62,
    ]

    report = "\n".join(lines)
    print(report)
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"\n  [SAVED] {OUT_REPORT}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "=" * 62)
    print("  Flood-Aware Evacuation Router  |  Kopili Basin, Assam")
    print("=" * 62)
    print(f"  Origin  : {VILLAGE_LATLON} (village)")
    print(f"  Dest    : {SHELTER_LATLON} (flood shelter)")
    print(f"  Depth threshold: {DEPTH_THRESHOLD} m")

    # --- STEP 1: Load road network ---
    print("\n--- STEP 1: Loading Road Network ---")
    G = load_network(BBOX)

    # --- STEP 2: Sample flood depth on all edges ---
    print("\n--- STEP 2: Annotating Flood Depth on Edges ---")
    G = annotate_flood_depth(G, FLOOD_RASTER)

    # --- STEP 3: Snap origin/destination to nearest nodes ---
    print("\n--- STEP 3: Snapping to Graph Nodes ---")
    origin_node, d_o = nearest_node(G, VILLAGE_LATLON[1], VILLAGE_LATLON[0])
    dest_node,   d_d = nearest_node(G, SHELTER_LATLON[1], SHELTER_LATLON[0])
    print(f"  Origin node : {origin_node}  (snap dist {d_o*111320:.0f} m)")
    print(f"  Dest node   : {dest_node}   (snap dist {d_d*111320:.0f} m)")

    if origin_node == dest_node:
        raise RuntimeError("Origin and destination snap to the same node. "
                           "Check coordinates or expand BBOX.")

    # --- STEP 4: Route finding ---
    print("\n--- STEP 4: Dijkstra's Shortest Path ---")
    path, total_m, is_dry = find_dry_route(G, origin_node, dest_node)

    if not path:
        print("\n  [CRITICAL] No route found — origin and shelter are completely")
        print("  disconnected even allowing flooded roads.")
        print("  Recommendations:")
        print("    - Request helicopter evacuation (NDRF/Air Force)")
        print("    - Move to highest ground and signal rescuers")
        print("    - Call SDRF Assam: 1070")
        return

    route_status = "FULLY DRY" if is_dry else "MINIMUM-FLOOD (flooded segments present)"
    print(f"  Route found: {len(path)} nodes, {len(path)-1} segments")
    print(f"  Distance   : {total_m/1000:.2f} km")
    print(f"  Status     : {route_status}")

    # --- STEP 5: Extract segment-level data ---
    print("\n--- STEP 5: Extracting Route Segments ---")
    segments = extract_route_data(G, path)

    # --- STEP 6: Turn-by-turn instructions ---
    print("\n--- STEP 6: Turn-by-Turn Directions ---")
    instructions = generate_turn_by_turn(segments)

    # --- STEP 7: Export GeoJSON ---
    print("\n--- STEP 7: Exporting GeoJSON ---")
    export_geojson(segments, VILLAGE_LATLON, SHELTER_LATLON, is_dry, OUT_GEOJSON)

    # --- STEP 8: Visualize ---
    print("\n--- STEP 8: Generating Map ---")
    visualize_route(G, segments, VILLAGE_LATLON, SHELTER_LATLON,
                    is_dry, BBOX, OUT_PLOT)

    # --- STEP 9: Full text report ---
    print("\n--- STEP 9: Evacuation Report ---")
    save_report(instructions, segments, VILLAGE_LATLON, SHELTER_LATLON, is_dry)

    print("\n" + "=" * 62)
    print("  OUTPUT FILES:")
    for f in [OUT_GEOJSON, OUT_PLOT, OUT_REPORT]:
        sz = os.path.getsize(f) / 1024 if os.path.exists(f) else 0
        print(f"    {f:<35} ({sz:.1f} KB)")
    print("=" * 62)


if __name__ == "__main__":
    main()
