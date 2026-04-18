#!/usr/bin/env python3
"""Geocode addresses/postal codes to GeoJSON for Leaflet maps.

Uses OpenStreetMap Nominatim (free, no API key required).
Output is GeoJSON — compatible with Leaflet's L.geoJSON().

Usage:
    python3 geocode.py "123 Main St, Vancouver, BC"
    python3 geocode.py "V6B 1A1"
    python3 geocode.py --reverse 49.2827,-123.1207
    python3 geocode.py --collection "Vancouver" "Toronto" "Edmonton"
    python3 geocode.py --file locations.txt
    python3 geocode.py "Ottawa, ON" --output markers.geojson
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import re

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "OpenClaw-Geocoder/1.0"
RATE_LIMIT_SECONDS = 1.1  # Nominatim requires max 1 req/sec


def geocode(query):
    """Geocode a single address/postal code. Returns a GeoJSON Feature or None."""
    # Detect Canadian postal code pattern and add "Canada" for better results
    postal_pattern = re.compile(r'^[A-Za-z]\d[A-Za-z]\s*\d[A-Za-z]\d$')
    search_query = query.strip()
    if postal_pattern.match(search_query):
        search_query = f"{search_query}, Canada"

    params = urllib.parse.urlencode({
        "q": search_query,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
        "countrycodes": "",  # No restriction, but postal code hint helps
    })

    url = f"{NOMINATIM_URL}/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error geocoding '{query}': {e}", file=sys.stderr)
        return None

    if not data:
        print(f"No results for '{query}'", file=sys.stderr)
        return None

    result = data[0]
    lat = float(result["lat"])
    lng = float(result["lon"])

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lng, lat]  # GeoJSON is [lon, lat]
        },
        "properties": {
            "query": query.strip(),
            "display_name": result.get("display_name", ""),
            "lat": lat,
            "lng": lng,
            "type": result.get("type", ""),
            "importance": result.get("importance", 0),
        }
    }


def reverse_geocode(coords):
    """Reverse geocode coordinates to address. Returns a GeoJSON Feature or None."""
    parts = coords.split(",")
    if len(parts) != 2:
        print(f"Invalid coordinates: '{coords}'. Use format: lat,lng", file=sys.stderr)
        return None

    lat, lng = parts[0].strip(), parts[1].strip()
    params = urllib.parse.urlencode({
        "lat": lat,
        "lon": lng,
        "format": "jsonv2",
        "addressdetails": 1,
    })

    url = f"{NOMINATIM_URL}/reverse?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error reverse geocoding '{coords}': {e}", file=sys.stderr)
        return None

    if "error" in data:
        print(f"No results for coordinates '{coords}'", file=sys.stderr)
        return None

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(lng), float(lat)]
        },
        "properties": {
            "query": coords,
            "display_name": data.get("display_name", ""),
            "lat": float(lat),
            "lng": float(lng),
            "type": data.get("type", ""),
            "importance": data.get("importance", 0),
        }
    }


def make_collection(features):
    """Wrap features in a GeoJSON FeatureCollection."""
    return {
        "type": "FeatureCollection",
        "features": [f for f in features if f is not None]
    }


def main():
    parser = argparse.ArgumentParser(description="Geocode addresses to GeoJSON for Leaflet")
    parser.add_argument("queries", nargs="*", help="Address(es) or postal code(s) to geocode")
    parser.add_argument("--reverse", metavar="LAT,LNG", help="Reverse geocode coordinates to address")
    parser.add_argument("--file", metavar="FILE", help="File with one address per line")
    parser.add_argument("--collection", nargs="+", metavar="ADDR", help="Geocode multiple addresses into a FeatureCollection")
    parser.add_argument("--output", "-o", metavar="FILE", help="Save output to file")

    args = parser.parse_args()

    results = []

    if args.reverse:
        feature = reverse_geocode(args.reverse)
        results.append(feature)

    elif args.file:
        try:
            with open(args.file, "r") as f:
                lines = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(1)

        for i, line in enumerate(lines):
            if i > 0:
                time.sleep(RATE_LIMIT_SECONDS)
            feature = geocode(line)
            results.append(feature)
            if feature:
                print(f"✓ {line}", file=sys.stderr)
            else:
                print(f"✗ {line}", file=sys.stderr)

    elif args.collection:
        for i, addr in enumerate(args.collection):
            if i > 0:
                time.sleep(RATE_LIMIT_SECONDS)
            feature = geocode(addr)
            results.append(feature)
            if feature:
                print(f"✓ {addr}", file=sys.stderr)
            else:
                print(f"✗ {addr}", file=sys.stderr)

    elif args.queries:
        for i, q in enumerate(args.queries):
            if i > 0:
                time.sleep(RATE_LIMIT_SECONDS)
            feature = geocode(q)
            results.append(feature)

    else:
        parser.print_help()
        sys.exit(1)

    # Build output
    valid = [r for r in results if r is not None]
    if not valid:
        print("No results found.", file=sys.stderr)
        sys.exit(1)

    if len(valid) == 1 and not args.collection and not args.file:
        output = valid[0]
    else:
        output = make_collection(valid)

    json_str = json.dumps(output, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str + "\n")
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
