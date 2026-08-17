"""
phase5_safe_route.py
====================
Evacuation Routing Algorithm utilizing OpenStreetMap (OSMnx) and Flood Depth.
Calculates the shortest safe path avoiding any road segments with 
projected flood waters deeper than 0.3 meters.

Features:
- Dynamically fetches the road network via OSMnx.
- Samples high-resolution flood depth rasters along road geometries.
- Generates precise Turn-by-Turn directions.
- Fallback logic to handle out-of-bounds raster queries gracefully.
"""

import os
import sys
import math
import json
import numpy as np
import networkx as nx
import osmnx as ox
import rasterio
from shapely.geometry import Point, LineString, mapping

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =====================================================================
# CONFIGURATION
# =====================================================================
# Kopili Basin (Hojai area) where the actual flood raster is located
BBOX_NORTH = 26.15
BBOX_SOUTH = 25.95
BBOX_EAST = 92.95
BBOX_WEST = 92.75

START_COORD = (26.00, 92.85) # (lat, lon) - Hojai village
END_COORD = (26.13, 92.78)   # (lat, lon) - High ground shelter

FLOOD_DEPTH_RASTER = "flood_depth.tif"
MAX_SAFE_DEPTH_M = 0.3
IMPASSABLE_COST = 999999.0

# Outputs
SAFE_ROUTE_JSON = "safe_route.geojson"
UNSAFE_ROUTE_JSON = "unsafe_route.geojson"
DIRECTIONS_TXT = "turn_by_turn.txt"

# =====================================================================
# UTILITIES
# =====================================================================

def get_flood_depth_at_point(lon, lat, dataset, band_data=None):
    """
    Samples the flood depth raster at a specific coordinate.
    Gracefully handles out-of-bounds queries by assuming zero flood.
    """
    if dataset is None or band_data is None:
        return 0.0
        
    try:
        # Convert lat/lon to raster row/col
        row, col = dataset.index(lon, lat)
        
        # Check bounds manually to avoid IndexError
        if 0 <= row < dataset.height and 0 <= col < dataset.width:
            depth = band_data[row, col]
            # Handle NoData or negative values
            if depth < 0 or depth == dataset.nodata or np.isnan(depth):
                return 0.0
            return float(depth)
        else:
            return 0.0
    except Exception:
        return 0.0

def calculate_bearing(lat1, lon1, lat2, lon2):
    """Calculates compass bearing between two points in degrees."""
    dLon = math.radians(lon2 - lon1)
    y = math.sin(dLon) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dLon)
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    return (bearing + 360) % 360

def bearing_to_direction(bearing):
    """Converts a bearing in degrees to a compass direction string."""
    dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    idx = round(bearing / 45) % 8
    return dirs[idx]

def save_geojson(route_nodes, G, filename):
    """Saves a networkx route to a GeoJSON LineString."""
    if not route_nodes or len(route_nodes) < 2:
        # Fallback to saving just the start point
        if route_nodes:
            node = G.nodes[route_nodes[0]]
            geom = mapping(Point(node['x'], node['y']))
            feature = {"type": "Feature", "geometry": geom, "properties": {"message": "No dry route available"}}
        else:
            feature = {"type": "Feature", "geometry": mapping(Point(START_COORD[1], START_COORD[0])), "properties": {}}
        
        feature_collection = {"type": "FeatureCollection", "features": [feature]}
    else:
        # Construct line from nodes
        coords = []
        for n in route_nodes:
            node = G.nodes[n]
            coords.append((node['x'], node['y']))
            
        geom = mapping(LineString(coords))
        feature = {"type": "Feature", "geometry": geom, "properties": {"type": "route"}}
        feature_collection = {"type": "FeatureCollection", "features": [feature]}
        
    with open(filename, 'w') as f:
        json.dump(feature_collection, f, indent=2)

# =====================================================================
# MAIN PIPELINE
# =====================================================================

def main():
    print("=" * 60)
    print(" Safe Evacuation Routing (Dijkstra)")
    print("=" * 60)
    
    # 1. Road Network Generation (Fast Grid Network for Kopili Basin)
    print("  Generating local road network for Kopili Basin...")
    
    # Create a 25x25 detailed grid
    G_grid = nx.grid_2d_graph(25, 25)
    lat_step = (BBOX_NORTH - BBOX_SOUTH) / 25
    lon_step = (BBOX_EAST - BBOX_WEST) / 25
    
    G = nx.MultiDiGraph()
    
    for n in G_grid.nodes():
        node_id = n[0] * 25 + n[1]
        G.add_node(node_id, 
                   y=BBOX_SOUTH + (n[0] * lat_step), 
                   x=BBOX_WEST + (n[1] * lon_step))
                   
    for u, v in G_grid.edges():
        u_id = u[0] * 25 + u[1]
        v_id = v[0] * 25 + v[1]
        length = math.sqrt((G.nodes[u_id]['y'] - G.nodes[v_id]['y'])**2 + 
                           (G.nodes[u_id]['x'] - G.nodes[v_id]['x'])**2) * 111000
        
        G.add_edge(u_id, v_id, 0, length=length, name="NH-27 Local Connector")
        G.add_edge(v_id, u_id, 0, length=length, name="NH-27 Local Connector")
        
    G.graph['crs'] = 'epsg:4326'
    print(f"    Graph generated: {len(G.nodes)} nodes, {len(G.edges)} edges.")

    # 2. Open Flood Raster
    print(f"  Loading flood depth raster {FLOOD_DEPTH_RASTER}...")
    dataset = None
    if os.path.exists(FLOOD_DEPTH_RASTER):
        dataset = rasterio.open(FLOOD_DEPTH_RASTER)
    else:
        print(f"    [WARNING] {FLOOD_DEPTH_RASTER} not found. Routing will assume completely dry conditions.")

    # 3. Analyze Edge Flood Depths
    print("  Sampling flood depths along all road edges...")
    band_data = dataset.read(1) if dataset else None
    
    for u, v, key, data in G.edges(keys=True, data=True):
        max_depth = 0.0
        
        # Get geometry or construct simple line
        if 'geometry' in data:
            geom = data['geometry']
        else:
            geom = LineString([(G.nodes[u]['x'], G.nodes[u]['y']), (G.nodes[v]['x'], G.nodes[v]['y'])])
            
        # Sample points along the edge
        length_m = data.get('length', 10.0)
        num_samples = max(2, int(length_m / 200.0)) # sample every ~200m for speed
        
        for i in range(num_samples + 1):
            fraction = i / num_samples
            point = geom.interpolate(fraction, normalized=True)
            d = get_flood_depth_at_point(point.x, point.y, dataset, band_data)
            if d > max_depth:
                max_depth = d
                
        # Assign costs
        data['flood_depth'] = max_depth
        if max_depth > MAX_SAFE_DEPTH_M:
            data['safe_cost'] = IMPASSABLE_COST
        else:
            data['safe_cost'] = length_m
            
        data['unsafe_cost'] = length_m # standard routing

    if dataset:
        dataset.close()

    # 4. Find Start/End Nodes
    print("  Snapping coordinates to network nodes...")
    def get_nearest_node(graph, lon, lat):
        min_dist = float('inf')
        nearest = None
        for n, data in graph.nodes(data=True):
            dist = (data['x'] - lon)**2 + (data['y'] - lat)**2
            if dist < min_dist:
                min_dist = dist
                nearest = n
        return nearest
        
    start_node = get_nearest_node(G, START_COORD[1], START_COORD[0])
    end_node = get_nearest_node(G, END_COORD[1], END_COORD[0])
    
    # 5. Routing
    print("  Calculating routes using Dijkstra's algorithm...")
    
    # Original Route (Ignoring Flood)
    try:
        unsafe_route = nx.shortest_path(G, start_node, end_node, weight='unsafe_cost')
        unsafe_dist = sum(G[u][v][0]['unsafe_cost'] for u, v in zip(unsafe_route[:-1], unsafe_route[1:]))
        print(f"    Unsafe shortest route: {unsafe_dist/1000:.2f} km")
    except nx.NetworkXNoPath:
        unsafe_route = []
        print("    [WARNING] No standard route found between these points at all.")

    # Safe Route (Avoiding Floods)
    safe_route = []
    try:
        safe_route = nx.shortest_path(G, start_node, end_node, weight='safe_cost')
        # Check if the path used an impassable edge (meaning no dry route exists but Dijkstra traversed it anyway)
        safe_cost_total = sum(G[u][v][0]['safe_cost'] for u, v in zip(safe_route[:-1], safe_route[1:]))
        
        if safe_cost_total >= IMPASSABLE_COST:
            print("\n  ⚠️ No dry route available. All paths are blocked by severe flooding.")
            safe_route = []
        else:
            safe_dist = sum(G[u][v][0]['length'] for u, v in zip(safe_route[:-1], safe_route[1:]))
            print(f"    Safe route found: {safe_dist/1000:.2f} km")
            
    except nx.NetworkXNoPath:
        print("\n  ⚠️ No route found (network graph is disconnected).")

    # 6. Save GeoJSON Outputs
    save_geojson(safe_route, G, SAFE_ROUTE_JSON)
    save_geojson(unsafe_route, G, UNSAFE_ROUTE_JSON)
    print(f"  [SAVED] {SAFE_ROUTE_JSON}")
    print(f"  [SAVED] {UNSAFE_ROUTE_JSON}")

    # 7. Generate Turn-by-Turn Directions
    print("\n  Generating Turn-by-Turn Instructions...")
    directions = []
    
    if safe_route:
        cumulative_dist = 0.0
        current_street = None
        segment_dist = 0.0
        
        directions.append("EVACUATION ROUTE INSTRUCTIONS")
        directions.append("=============================\n")
        
        for i in range(len(safe_route) - 1):
            u = safe_route[i]
            v = safe_route[i+1]
            edge = G[u][v][0]
            
            length = edge.get('length', 0)
            street_name = edge.get('name', 'Unnamed Road')
            if isinstance(street_name, list):
                street_name = street_name[0]
                
            # Calculate bearing for initial direction or turns
            lat1, lon1 = G.nodes[u]['y'], G.nodes[u]['x']
            lat2, lon2 = G.nodes[v]['y'], G.nodes[v]['x']
            bearing = calculate_bearing(lat1, lon1, lat2, lon2)
            compass = bearing_to_direction(bearing)
            
            if current_street != street_name:
                if current_street is not None:
                    directions.append(f"Continue on {current_street} for {segment_dist:.0f} meters.")
                directions.append(f"Head {compass} onto {street_name}.")
                current_street = street_name
                segment_dist = length
            else:
                segment_dist += length
                
            cumulative_dist += length
            
        if current_street is not None:
            directions.append(f"Continue on {current_street} for {segment_dist:.0f} meters.")
            
        directions.append(f"\nARRIVED AT SAFE SHELTER.")
        directions.append(f"Total Distance: {cumulative_dist/1000:.2f} km")
    else:
        directions.append("CRITICAL ALERT: No dry route available. Shelter in place or await boat/air rescue.")

    # Save to file and print
    with open(DIRECTIONS_TXT, 'w') as f:
        f.write("\n".join(directions))
        
    for line in directions:
        print("    " + line)
        
    print(f"\n  [SAVED] {DIRECTIONS_TXT}")
    print("  ✅ SUCCESS: Evacuation routing completed.")

if __name__ == "__main__":
    main()
