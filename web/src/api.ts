import type { ProjectOut, SiteSummaryOut, WellSummaryOut } from "./types";

const API_BASE = "/api";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchProjects(): Promise<ProjectOut[]> {
  return getJson(`${API_BASE}/projects`);
}

export function fetchSiteSummary(siteId: string): Promise<SiteSummaryOut> {
  const params = new URLSearchParams({ site_id: siteId });
  return getJson(`${API_BASE}/sites/summary?${params}`);
}

export function fetchWellSummary(wellId: string): Promise<WellSummaryOut> {
  const params = new URLSearchParams({ well_id: wellId });
  return getJson(`${API_BASE}/wells/summary?${params}`);
}
