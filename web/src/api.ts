import type {
  ProjectOut,
  ProjectWrite,
  ReachOut,
  ReachWrite,
  SiteOut,
  SiteSummaryOut,
  SiteWrite,
  WellOut,
  WellReadingsOut,
  WellSummaryOut,
  WellWrite,
} from "./types";

const API_BASE = "/api";

async function handleResponse<T>(response: Response, path: string): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON - fall back to statusText
    }
    throw new Error(`${path} failed: ${response.status} ${detail}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

async function getJson<T>(path: string): Promise<T> {
  return handleResponse<T>(await fetch(path), path);
}

async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handleResponse<T>(response, path);
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

export function fetchWellReadings(wellId: string, parameter: string, from?: Date): Promise<WellReadingsOut> {
  const params = new URLSearchParams({ well_id: wellId, parameter });
  if (from) params.set("from", from.toISOString());
  return getJson(`${API_BASE}/wells/readings?${params}`);
}

// ---- Project management ------------------------------------------------

export function createProject(body: ProjectWrite): Promise<ProjectOut> {
  return sendJson("POST", `${API_BASE}/projects`, body);
}

export function updateProject(projectId: string, body: ProjectWrite): Promise<ProjectOut> {
  const params = new URLSearchParams({ project_id: projectId });
  return sendJson("PATCH", `${API_BASE}/projects?${params}`, body);
}

export function deleteProject(projectId: string): Promise<void> {
  const params = new URLSearchParams({ project_id: projectId });
  return sendJson("DELETE", `${API_BASE}/projects?${params}`);
}

// ---- Reach management --------------------------------------------------

export function createReach(projectId: string, body: ReachWrite): Promise<ReachOut> {
  const params = new URLSearchParams({ project_id: projectId });
  return sendJson("POST", `${API_BASE}/reaches?${params}`, body);
}

export function updateReach(reachId: string, body: ReachWrite): Promise<ReachOut> {
  const params = new URLSearchParams({ reach_id: reachId });
  return sendJson("PATCH", `${API_BASE}/reaches?${params}`, body);
}

export function deleteReach(reachId: string): Promise<void> {
  const params = new URLSearchParams({ reach_id: reachId });
  return sendJson("DELETE", `${API_BASE}/reaches?${params}`);
}

// ---- Site management ----------------------------------------------------

export function createSite(reachId: string, body: SiteWrite): Promise<SiteOut> {
  const params = new URLSearchParams({ reach_id: reachId });
  return sendJson("POST", `${API_BASE}/sites?${params}`, body);
}

export function updateSite(siteId: string, body: SiteWrite): Promise<SiteOut> {
  const params = new URLSearchParams({ site_id: siteId });
  return sendJson("PATCH", `${API_BASE}/sites?${params}`, body);
}

export function deleteSite(siteId: string): Promise<void> {
  const params = new URLSearchParams({ site_id: siteId });
  return sendJson("DELETE", `${API_BASE}/sites?${params}`);
}

// ---- Well management ----------------------------------------------------

export function createWell(siteId: string, body: WellWrite): Promise<WellOut> {
  const params = new URLSearchParams({ site_id: siteId });
  return sendJson("POST", `${API_BASE}/wells?${params}`, body);
}

export function updateWell(wellId: string, body: WellWrite): Promise<WellOut> {
  const params = new URLSearchParams({ well_id: wellId });
  return sendJson("PATCH", `${API_BASE}/wells?${params}`, body);
}

export function deleteWell(wellId: string): Promise<void> {
  const params = new URLSearchParams({ well_id: wellId });
  return sendJson("DELETE", `${API_BASE}/wells?${params}`);
}
