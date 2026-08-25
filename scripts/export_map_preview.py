#!/usr/bin/env python
"""Builds a private, local, self-contained map preview (preview/map.html).

NOT published anywhere - not deployed to Netlify, not a Claude Artifact,
nothing touches the live TWC site or its real Google Sheet. Just double-
click preview/map.html to open it in a browser. All three layers' data
is embedded directly in the file (no live fetch, no server needed):

  - TWC (Live)  - a snapshot of the real production events, fetched
    read-only from the live public API at generation time. Nothing is
    ever written back - this only reads.
  - TriviaNearMe - this project's directory-sourced candidate data
    (confidence 40, unverified).
  - Verified - this project's first-party-sourced/corroborated data
    (confidence >= 60), across all adapters including DJ Trivia.

Each is its own togglable layer so you can turn any of them on/off
independently while looking at the map. Since this file never leaves
your machine, "turning a layer off" here is just a checkbox - there's no
real-user-visibility concern to gate, unlike anything on the live site.

Usage:
    python scripts/export_map_preview.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from sqlalchemy import select

from crawler.db import get_session
from crawler.models import EventStatus, Source, SourceType, TriviaEvent, Venue

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "preview" / "map.html"
TWC_LIVE_EVENTS_URL = "https://triviawriterscoop.com/.netlify/functions/list-in-person-events"


def fetch_twc_live() -> list[dict]:
    print("Fetching live TWC events (read-only snapshot)...")
    try:
        resp = requests.get(TWC_LIVE_EVENTS_URL, timeout=20)
        resp.raise_for_status()
        events = resp.json()
    except requests.RequestException as exc:
        print(f"  [warn] could not fetch live TWC events: {exc}")
        return []

    out = []
    for e in events:
        if e.get("lat") is None or e.get("lng") is None:
            continue
        out.append(
            {
                "name": e.get("venueName"),
                "lat": e["lat"],
                "lng": e["lng"],
                "day": e.get("day"),
                "time": e.get("time"),
                "provider": e.get("companyName"),
                "address": e.get("address"),
                "city": e.get("city"),
                "region": e.get("state"),
                "certified": bool(e.get("certified")),
            }
        )
    print(f"  {len(out)} live TWC events with coordinates")
    return out


def fetch_tnm_candidates(session) -> list[dict]:
    stmt = (
        select(TriviaEvent)
        .join(Venue)
        .join(Source)
        .where(Source.name == "TriviaNearMe", Venue.latitude.is_not(None))
    )
    events = session.execute(stmt).scalars().unique().all()
    out = []
    for e in events:
        v = e.venue
        out.append(
            {
                "name": v.name,
                "lat": v.latitude,
                "lng": v.longitude,
                "day": e.day_of_week,
                "time": e.start_time,
                "provider": e.provider.name if e.provider else e.raw_provider_name,
                "address": v.street_address,
                "city": v.city,
                "region": v.region,
                "confidence": e.confidence_score,
            }
        )
    print(f"  {len(out)} TriviaNearMe candidates with coordinates")
    return out


def fetch_verified(session) -> list[dict]:
    stmt = (
        select(TriviaEvent)
        .join(Venue)
        .join(Source)
        .where(
            TriviaEvent.confidence_score >= 60,
            TriviaEvent.status == EventStatus.ACTIVE,
            Venue.latitude.is_not(None),
        )
    )
    events = session.execute(stmt).scalars().unique().all()
    out = []
    for e in events:
        v = e.venue
        out.append(
            {
                "name": v.name,
                "lat": v.latitude,
                "lng": v.longitude,
                "day": e.day_of_week,
                "time": e.start_time,
                "provider": e.provider.name if e.provider else e.raw_provider_name,
                "address": v.street_address,
                "city": v.city,
                "region": v.region,
                "confidence": e.confidence_score,
                "source": e.source.name if e.source else None,
            }
        )
    print(f"  {len(out)} verified events with coordinates")
    return out


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Trivia Crawler - Map Preview (private, not published)</title>
<meta name="robots" content="noindex, nofollow" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
<style>
  :root {
    --navy: #1e3a5f;
    --gold: #c5a572;
    --gray: #6b7280;
    --green: #2e7d4f;
  }
  html, body { margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  #map { position: absolute; top: 64px; left: 0; right: 0; bottom: 0; }
  header {
    height: 64px; background: var(--navy); color: #fff; display: flex; align-items: center;
    gap: 24px; padding: 0 20px; box-sizing: border-box; font-size: 14px;
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; white-space: nowrap; }
  header .badge { background: #b23b3b; color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; text-transform: uppercase; letter-spacing: 0.03em; }
  .layer-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
  .layer-toggle input { cursor: pointer; }
  .swatch { width: 12px; height: 12px; border-radius: 50%; display: inline-block; border: 2px solid #fff; }
  .swatch.twc { background: var(--gold); }
  .swatch.tnm { background: var(--gray); }
  .swatch.verified { background: var(--green); }
  .counts { margin-left: auto; font-size: 12px; opacity: 0.85; white-space: nowrap; }
  .leaflet-popup-content b { color: var(--navy); }
  .conf { font-size: 11px; color: #888; }
</style>
</head>
<body>
<header>
  <h1>Trivia Crawler Map Preview</h1>
  <span class="badge">Private &middot; Not Published</span>
  <label class="layer-toggle"><input type="checkbox" id="toggle-twc" checked /><span class="swatch twc"></span>TWC (Live)</label>
  <label class="layer-toggle"><input type="checkbox" id="toggle-tnm" checked /><span class="swatch tnm"></span>TriviaNearMe (candidates)</label>
  <label class="layer-toggle"><input type="checkbox" id="toggle-verified" checked /><span class="swatch verified"></span>Verified (first-party)</label>
  <span class="counts" id="counts"></span>
</header>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const TWC_DATA = __TWC_DATA__;
const TNM_DATA = __TNM_DATA__;
const VERIFIED_DATA = __VERIFIED_DATA__;

const map = L.map('map').setView([39.5, -98.35], 4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 19
}).addTo(map);

function popupHtml(item, sourceLabel) {
  const parts = [`<b>${escapeHtml(item.name || 'Unknown venue')}</b>`];
  if (item.provider) parts.push(escapeHtml(item.provider));
  if (item.day || item.time) parts.push(`${escapeHtml(item.day || '')} ${escapeHtml(item.time || '')}`.trim());
  const addr = [item.address, item.city, item.region].filter(Boolean).join(', ');
  if (addr) parts.push(escapeHtml(addr));
  if (item.certified) parts.push('<span style="color:#c5a572;font-weight:600;">TWC Certified</span>');
  if (typeof item.confidence === 'number') parts.push(`<span class="conf">confidence: ${item.confidence} &middot; ${escapeHtml(item.source || sourceLabel)}</span>`);
  return parts.join('<br>');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function buildLayer(data, color, sourceLabel) {
  const cluster = L.markerClusterGroup({ maxClusterRadius: 40, disableClusteringAtZoom: 14 });
  data.forEach(item => {
    if (typeof item.lat !== 'number' || typeof item.lng !== 'number') return;
    const marker = L.circleMarker([item.lat, item.lng], {
      radius: 6, color: '#fff', weight: 1.5, fillColor: color, fillOpacity: 0.9
    });
    marker.bindPopup(popupHtml(item, sourceLabel));
    cluster.addLayer(marker);
  });
  return cluster;
}

const twcLayer = buildLayer(TWC_DATA, '#c5a572', 'TWC');
const tnmLayer = buildLayer(TNM_DATA, '#6b7280', 'TriviaNearMe');
const verifiedLayer = buildLayer(VERIFIED_DATA, '#2e7d4f', 'Verified');

twcLayer.addTo(map);
tnmLayer.addTo(map);
verifiedLayer.addTo(map);

document.getElementById('toggle-twc').addEventListener('change', e => {
  e.target.checked ? map.addLayer(twcLayer) : map.removeLayer(twcLayer);
});
document.getElementById('toggle-tnm').addEventListener('change', e => {
  e.target.checked ? map.addLayer(tnmLayer) : map.removeLayer(tnmLayer);
});
document.getElementById('toggle-verified').addEventListener('change', e => {
  e.target.checked ? map.addLayer(verifiedLayer) : map.removeLayer(verifiedLayer);
});

document.getElementById('counts').textContent =
  `TWC: ${TWC_DATA.length} | TriviaNearMe: ${TNM_DATA.length} | Verified: ${VERIFIED_DATA.length}`;
</script>
</body>
</html>
"""


def main() -> None:
    with get_session() as session:
        tnm = fetch_tnm_candidates(session)
        verified = fetch_verified(session)
    twc = fetch_twc_live()

    html = (
        HTML_TEMPLATE.replace("__TWC_DATA__", json.dumps(twc))
        .replace("__TNM_DATA__", json.dumps(tnm))
        .replace("__VERIFIED_DATA__", json.dumps(verified))
    )

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    print("Open it directly in a browser - nothing here is published or deployed anywhere.")


if __name__ == "__main__":
    main()
