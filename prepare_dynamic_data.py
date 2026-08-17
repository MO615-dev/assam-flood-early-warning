import os
import sys
import json
import math
import numpy as np
import rasterio
from rasterio.features import shapes as rio_shapes
from shapely.geometry import shape, mapping, Point, LineString
from shapely.ops import unary_union

# Force UTF-8 console output to avoid encoding errors on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HAND_PATH = "hand.tif"
OUT_JSON = os.path.join("webapp", "data", "dynamic_data.json")

# Ensure directory exists
os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

def generate_dynamic_flood_geojson():
    print("=== Generating Optimized Dynamic Flood Polygons ===")
    if not os.path.exists(HAND_PATH):
        print(f"[ERROR] {HAND_PATH} not found!")
        return {}

    dynamic_flood = {}
    with rasterio.open(HAND_PATH) as src:
        hand = src.read(1)
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999.0

        # Define stage levels to precompute (1.0m to 12.0m in steps of 0.5m)
        stages = np.arange(1.0, 12.5, 0.5)

        for h in stages:
            h_str = f"{h:.1f}"
            
            # Calculate depth: h - hand
            valid_mask = (hand != nodata) & (~np.isnan(hand))
            depth = np.where(valid_mask, h - hand, -9999.0)

            # Classify depth:
            # 5: depth <= 0 (safe dry land)
            # 1: 0.0 < depth <= 0.5m
            # 2: 0.5m < depth <= 1.5m
            # 3: 1.5m < depth <= 3.0m
            # 4: depth > 3.0m
            classified = np.zeros_like(depth, dtype=np.uint8)
            classified[valid_mask & (depth <= 0)] = 5
            classified[valid_mask & (depth > 0) & (depth <= 0.5)] = 1
            classified[valid_mask & (depth > 0.5) & (depth <= 1.5)] = 2
            classified[valid_mask & (depth > 1.5) & (depth <= 3.0)] = 3
            classified[valid_mask & (depth > 3.0)] = 4

            # Subsample by 8x (approx 720m cells) to keep sizes extremely compact
            sub_classified = classified[::8, ::8]
            sub_transform = transform * rasterio.Affine.scale(8, 8)

            stage_features = []

            for class_val, depth_class, color in [
                (5, "safe", "#10b981"),      
                (1, "low", "#fbbf24"),       
                (2, "medium", "#f59e0b"),    
                (3, "high", "#ef4444"),      
                (4, "extreme", "#991b1b")    
            ]:
                mask = (sub_classified == class_val)
                polys = []
                for geom, val in rio_shapes(sub_classified, mask=mask, transform=sub_transform):
                    if val == class_val:
                        polys.append(shape(geom))

                if polys:
                    merged = unary_union(polys)
                    # Simplify aggressively for safe land to control JSON file size
                    tol = 0.0055 if class_val == 5 else 0.0035
                    simplified = merged.simplify(tol, preserve_topology=True)
                    
                    geoms = []
                    if simplified.geom_type == "Polygon":
                        geoms = [simplified]
                    elif simplified.geom_type == "MultiPolygon":
                        geoms = list(simplified.geoms)

                    for g in geoms:
                        # Keep only larger bodies to reduce payload size
                        min_area = 0.0006 if class_val == 5 else 0.0001
                        if g.area > min_area:  
                            stage_features.append({
                                "type": "Feature",
                                "geometry": mapping(g),
                                "properties": {
                                    "class": 0 if class_val == 5 else class_val,
                                    "depth_class": depth_class,
                                    "color": color
                                }
                            })
            
            dynamic_flood[h_str] = stage_features
            print(f"  Stage {h_str} m: Generated {len(stage_features)} polygons")

    return dynamic_flood

def generate_road_network():
    print("\n=== Generating Road Network ===")
    if not os.path.exists(HAND_PATH):
        print(f"[ERROR] {HAND_PATH} not found!")
        return {}

    # Define study bounds (Kopili Basin - Hojai area)
    min_lon, max_lon = 92.72, 92.95
    min_lat, max_lat = 25.95, 26.15

    # Grid parameters
    cols, rows = 12, 12  
    lon_step = (max_lon - min_lon) / (cols - 1)
    lat_step = (max_lat - min_lat) / (rows - 1)

    nodes = {}
    edges = []

    # Generate nodes
    node_idx = 0
    for r in range(rows):
        for c in range(cols):
            lon = min_lon + c * lon_step
            lat = min_lat + r * lat_step
            
            # Snap start node (Village)
            if r == 3 and c == 7:
                lon, lat = 92.85, 26.00
            # Snap end node (Shelter)
            if r == 9 and c == 3:
                lon, lat = 92.78, 26.13

            nodes[node_idx] = {
                "id": node_idx,
                "x": round(lon, 5),
                "y": round(lat, 5)
            }
            node_idx += 1

    # Open raster to sample HAND values
    with rasterio.open(HAND_PATH) as src:
        hand = src.read(1)
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999.0

        def get_min_hand_on_edge(lon1, lat1, lon2, lat2):
            # Sample 6 points along the edge and return the MINIMUM hand value
            # This represents the lowest, most flood-vulnerable point of the road segment
            min_val = 9999.0
            for frac in np.linspace(0, 1, 6):
                x = lon1 + frac * (lon2 - lon1)
                y = lat1 + frac * (lat2 - lat1)
                try:
                    row, col = src.index(x, y)
                    if 0 <= row < src.height and 0 <= col < src.width:
                        val = hand[row, col]
                        if val != nodata and not np.isnan(val):
                            min_val = min(min_val, float(val))
                except Exception:
                    pass
            return round(min_val, 2) if min_val != 9999.0 else 0.0

        # Build grid connectivity
        for r in range(rows):
            for c in range(cols):
                u_idx = r * cols + c
                
                # Horizontal connections
                if c < cols - 1:
                    v_idx = u_idx + 1
                    u_node = nodes[u_idx]
                    v_node = nodes[v_idx]
                    length_m = math.sqrt((u_node["x"] - v_node["x"])**2 + (u_node["y"] - v_node["y"])**2) * 111320
                    min_hand = get_min_hand_on_edge(u_node["x"], u_node["y"], v_node["x"], v_node["y"])
                    
                    if r == 6:
                        name = "National Highway 27"
                    elif r == 3:
                        name = "Lanka-Hojai Connector Road"
                    elif r == 9:
                        name = "State Highway 18"
                    else:
                        name = f"Village Path {r}-{c}"

                    edges.append({
                        "from": u_idx,
                        "to": v_idx,
                        "length": round(length_m, 1),
                        "min_hand": min_hand,
                        "name": name,
                        "geometry": [[u_node["x"], u_node["y"]], [v_node["x"], v_node["y"]]]
                    })

                # Vertical connections
                if r < rows - 1:
                    v_idx = u_idx + cols
                    u_node = nodes[u_idx]
                    v_node = nodes[v_idx]
                    length_m = math.sqrt((u_node["x"] - v_node["x"])**2 + (u_node["y"] - v_node["y"])**2) * 111320
                    min_hand = get_min_hand_on_edge(u_node["x"], u_node["y"], v_node["x"], v_node["y"])
                    
                    if c == 3:
                        name = "Lanka Bypass Highway"
                    elif c == 7:
                        name = "Hojai Main Road"
                    else:
                        name = f"Local Connector {r}-{c}"

                    edges.append({
                        "from": u_idx,
                        "to": v_idx,
                        "length": round(length_m, 1),
                        "min_hand": min_hand,
                        "name": name,
                        "geometry": [[u_node["x"], u_node["y"]], [v_node["x"], v_node["y"]]]
                    })

    print(f"  Generated {len(nodes)} nodes, {len(edges)} edges.")
    return {
        "nodes": list(nodes.values()),
        "edges": edges
    }

def generate_osm_road_network():
    print("\n=== Generating OSM Road Network ===")
    if not os.path.exists(HAND_PATH):
        print(f"[ERROR] {HAND_PATH} not found!")
        return None

    try:
        import osmnx as ox
        import networkx as nx
        from shapely.geometry import LineString
        
        # Define study bounds (Kopili Basin - Hojai area)
        min_lon, max_lon = 92.72, 92.95
        min_lat, max_lat = 25.95, 26.15
        
        ox.settings.requests_timeout = 60
        ox.settings.timeout = 60
        print("  Downloading road network from OpenStreetMap...")
        # Include major drivable roads
        osm_filter = (
            '["highway"~"motorway|trunk|primary|secondary|tertiary|'
            'unclassified|residential|road"]'
        )
        G = ox.graph_from_bbox(
            bbox=(min_lon, min_lat, max_lon, max_lat),
            network_type="drive",
            custom_filter=osm_filter
        )
        G = ox.convert.to_undirected(G)
        print(f"  OSM network fetched: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")
    except Exception as exc:
        print(f"  [WARN] Failed to fetch OSM network: {exc}")
        print("  Falling back to synthetic grid road network...")
        return None

    # Open raster to sample HAND values
    with rasterio.open(HAND_PATH) as src:
        hand = src.read(1)
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999.0

        def get_min_hand_on_edge(geom):
            min_val = 9999.0
            for frac in np.linspace(0, 1, 6):
                pt = geom.interpolate(frac, normalized=True)
                try:
                    row, col = src.index(pt.x, pt.y)
                    if 0 <= row < src.height and 0 <= col < src.width:
                        val = hand[row, col]
                        if val != nodata and not np.isnan(val):
                            min_val = min(min_val, float(val))
                except Exception:
                    pass
            return round(min_val, 2) if min_val != 9999.0 else 0.0

        # Map nodes
        osm_node_ids = list(G.nodes)
        node_map = {osm_id: idx for idx, osm_id in enumerate(osm_node_ids)}
        
        web_nodes = []
        for osm_id in osm_node_ids:
            node_data = G.nodes[osm_id]
            web_nodes.append({
                "id": node_map[osm_id],
                "x": round(node_data["x"], 5),
                "y": round(node_data["y"], 5)
            })

        web_edges = []
        seen_pairs = {}
        
        for u, v, data in G.edges(data=True):
            u_web = node_map[u]
            v_web = node_map[v]
            pair = tuple(sorted([u_web, v_web]))
            
            length = data.get("length", 100.0)
            
            # Reconstruct geometry if missing
            geom = data.get("geometry")
            if geom is None or geom.is_empty:
                x_u, y_u = G.nodes[u]["x"], G.nodes[u]["y"]
                x_v, y_v = G.nodes[v]["x"], G.nodes[v]["y"]
                geom = LineString([(x_u, y_u), (x_v, y_v)])
                
            min_hand = get_min_hand_on_edge(geom)
            
            name = data.get("name", "Local Road")
            if isinstance(name, list):
                name = name[0]
            name = str(name)
            
            # Map coords to list of [lon, lat] pairs
            coords = [[round(coord[0], 5), round(coord[1], 5)] for coord in geom.coords]
            
            edge_dict = {
                "from": u_web,
                "to": v_web,
                "length": round(length, 1),
                "min_hand": min_hand,
                "name": name,
                "geometry": coords
            }
            
            if pair in seen_pairs:
                if length < seen_pairs[pair]["length"]:
                    seen_pairs[pair] = edge_dict
            else:
                seen_pairs[pair] = edge_dict
                
        web_edges = list(seen_pairs.values())

    return {
        "nodes": web_nodes,
        "edges": web_edges
    }

def main():
    dynamic_flood = generate_dynamic_flood_geojson()
    
    # Try to load real OSM roads first, fall back to synthetic grid if fails
    road_network = generate_osm_road_network()
    if road_network is None:
        road_network = generate_road_network()
    
    data = {
        "dynamic_flood": dynamic_flood,
        "road_network": road_network
    }
    
    print(f"\nWriting to {OUT_JSON}...")
    with open(OUT_JSON, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    
    size_mb = os.path.getsize(OUT_JSON) / (1024 * 1024)
    print(f"SUCCESS: Created dynamic data bundle ({size_mb:.2f} MB)")

if __name__ == "__main__":
    main()
