---
name: geocoder
description: Convert addresses, postal codes, or place names into geographic coordinates (latitude/longitude) for Leaflet map applications. Returns GeoJSON format ready for L.geoJSON(). Uses OpenStreetMap's free Nominatim API — no API key required. Triggers on requests involving geocoding, coordinates, mapping, Leaflet, or location lookup.
---

# Geocoder

Convert addresses, postal codes, and place names to geographic coordinates for Leaflet.js maps.

## Quick Commands

```bash
# Geocode an address
python3 {baseDir}/scripts/geocode.py "123 Main Street, Vancouver, BC"

# Geocode a Canadian postal code
python3 {baseDir}/scripts/geocode.py "V6B 1A1"

# Geocode multiple locations (comma-separated file, one per line)
python3 {baseDir}/scripts/geocode.py --file locations.txt

# Reverse geocode (coordinates to address)
python3 {baseDir}/scripts/geocode.py --reverse 49.2827,-123.1207

# Output as Leaflet-ready GeoJSON FeatureCollection
python3 {baseDir}/scripts/geocode.py --collection "Vancouver, BC" "Toronto, ON" "Edmonton, AB"

# Save output to file
python3 {baseDir}/scripts/geocode.py "Ottawa, ON" --output markers.geojson
```

## Output Format

Returns GeoJSON — drop directly into Leaflet's `L.geoJSON()`:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [-123.1207, 49.2827]
  },
  "properties": {
    "query": "Vancouver, BC",
    "display_name": "Vancouver, British Columbia, Canada",
    "lat": 49.2827,
    "lng": -123.1207,
    "type": "city",
    "importance": 0.78
  }
}
```

## Leaflet Usage

```javascript
// Single marker
fetch('markers.geojson')
  .then(r => r.json())
  .then(data => L.geoJSON(data).addTo(map));

// With popups
L.geoJSON(data, {
  onEachFeature: (feature, layer) => {
    layer.bindPopup(feature.properties.display_name);
  }
}).addTo(map);
```

## Notes

- Uses OpenStreetMap Nominatim (free, no API key)
- Rate limited to 1 request/second per Nominatim usage policy
- Best results with Canadian addresses and postal codes
- GeoJSON coordinates are `[longitude, latitude]` (GeoJSON spec)
