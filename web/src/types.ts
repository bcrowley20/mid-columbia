// Mirrors src/midcolumbia/api/schemas.py. Keep in sync by hand for now - see
// Implementation Plan.md section 12 (no shared schema generation in v1).

export interface WellOut {
  id: string;
  name: string;
  well_type: string;
  device_serial: string | null;
  paired_atm_well_id: string | null;
  // Only meaningful for a reach-level ATM well - a Site-affiliated well's
  // location is its parent SiteOut's latitude/longitude instead.
  latitude: number | null;
  longitude: number | null;
}

export interface SiteOut {
  id: string;
  name: string;
  latitude: number | null;
  longitude: number | null;
  wells: WellOut[];
}

export interface ReachOut {
  id: string;
  name: string;
  atm_well: WellOut;
  sites: SiteOut[];
}

export interface ProjectOut {
  id: string;
  name: string;
  reaches: ReachOut[];
}

export interface WellSummaryOut {
  well_id: string;
  well_name: string;
  well_type: string;
  point_count: number;
  last_reading_at: string | null;
}

export interface SiteSummaryOut {
  site_id: string;
  site_name: string;
  reach_id: string;
  reach_name: string;
  latitude: number | null;
  longitude: number | null;
  wells: WellSummaryOut[];
}
