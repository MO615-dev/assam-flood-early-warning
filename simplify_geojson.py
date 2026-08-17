import json, os
from shapely.geometry import shape, mapping

with open('webapp/data/flood_extent.geojson') as f:
    gj = json.load(f)

geom = shape(gj['features'][0]['geometry'])
simplified = geom.simplify(0.005, preserve_topology=True)
gj['features'][0]['geometry'] = mapping(simplified)

out = 'webapp/data/flood_extent.geojson'
with open(out, 'w') as f:
    json.dump(gj, f, separators=(',', ':'))

size = os.path.getsize(out) / 1024
print(f'Simplified flood GeoJSON: {size:.0f} KB')
