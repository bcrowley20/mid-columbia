import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { fetchSiteSummary } from "./api";
import type { ReachOut, SiteSummaryOut } from "./types";

// No default view configured yet (project.json5's map.center/zoom isn't wired
// through the API - Phase 4 uses fitBounds on real site coordinates instead,
// see Implementation Plan.md section 12). This is only the pre-selection
// fallback, roughly centered on the one real reference point we have so far.
const FALLBACK_CENTER: L.LatLngExpression = [47.2547, -120.9048];
const FALLBACK_ZOOM = 12;
const SINGLE_SITE_ZOOM = 15;

export class SiteMap {
  private readonly map: L.Map;
  private readonly markersLayer: L.LayerGroup;
  private readonly emptyStateEl: HTMLElement;

  constructor(container: HTMLElement, emptyStateEl: HTMLElement) {
    this.emptyStateEl = emptyStateEl;
    this.map = L.map(container).setView(FALLBACK_CENTER, FALLBACK_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);
    this.markersLayer = L.layerGroup().addTo(this.map);
  }

  async showReach(reach: ReachOut): Promise<void> {
    this.markersLayer.clearLayers();

    const locatedSites = reach.sites.filter(
      (site): site is typeof site & { latitude: number; longitude: number } =>
        site.latitude !== null && site.longitude !== null,
    );

    if (locatedSites.length === 0) {
      this.showEmptyState(`No sites in "${reach.name}" have a location set yet.`);
      return;
    }
    this.hideEmptyState();

    // Fetch every site's summary up front (a handful of requests per reach)
    // so hover tooltips are instant rather than round-tripping on mouseover.
    const summaries = await Promise.all(locatedSites.map((site) => fetchSiteSummary(site.id)));

    const points: L.LatLngExpression[] = [];
    locatedSites.forEach((site, index) => {
      const latLng: L.LatLngExpression = [site.latitude, site.longitude];
      points.push(latLng);

      const marker = L.circleMarker(latLng, {
        radius: 8,
        color: "#ffffff",
        weight: 2,
        fillColor: "#2563eb",
        fillOpacity: 0.9,
      });
      marker.bindTooltip(renderSitePopup(summaries[index]), { direction: "top", offset: [0, -8] });
      marker.addTo(this.markersLayer);
    });

    if (points.length === 1) {
      this.map.setView(points[0], SINGLE_SITE_ZOOM);
    } else {
      this.map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
    }
  }

  private showEmptyState(message: string): void {
    this.emptyStateEl.textContent = message;
    this.emptyStateEl.hidden = false;
  }

  private hideEmptyState(): void {
    this.emptyStateEl.hidden = true;
  }
}

function renderSitePopup(summary: SiteSummaryOut): string {
  const wellRows = summary.wells
    .map(
      (well) => `
        <div class="popup-well">
          <span class="popup-well-name">${escapeHtml(well.well_name)}</span>
          <span class="popup-well-stat">${well.point_count.toLocaleString()} pts</span>
          <span class="popup-well-stat">${formatTimestamp(well.last_reading_at)}</span>
        </div>`,
    )
    .join("");

  return `
    <div class="site-popup">
      <div class="site-popup-title">${escapeHtml(summary.reach_name)} &rsaquo; ${escapeHtml(summary.site_name)}</div>
      ${wellRows}
    </div>`;
}

function formatTimestamp(value: string | null): string {
  if (value === null) {
    return "no data";
  }
  return new Date(value).toLocaleString();
}

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
