// Site Management UI (Phase 5): create/edit/delete forms for
// Project/Reach/Site/Well, backed by the management endpoints in api.ts.
// One shared <dialog> (see index.html) is reused for every entity/mode -
// see openEntityDialog for how it avoids stale event listeners piling up.

import * as api from "./api";
import type { ProjectOut, ReachOut, SiteOut, WellOut } from "./types";

interface FieldSpec {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  required?: boolean;
  step?: string;
  options?: { value: string; label: string }[];
}

type FieldValues = Record<string, string | number | null>;

const PROJECT_FIELDS: FieldSpec[] = [
  { key: "name", label: "Project name", type: "text", required: true },
  { key: "description", label: "Description", type: "text" },
  { key: "timezone", label: "Timezone (IANA, e.g. America/Los_Angeles)", type: "text", required: true },
  { key: "map_center_lat", label: "Default map center latitude", type: "number", step: "any" },
  { key: "map_center_lon", label: "Default map center longitude", type: "number", step: "any" },
  { key: "map_zoom", label: "Default map zoom", type: "number" },
];

const REACH_FIELDS: FieldSpec[] = [
  { key: "name", label: "Reach name", type: "text", required: true },
  { key: "atm_name", label: "ATM (atmospheric) well name", type: "text", required: true },
  { key: "atm_device_serial", label: "ATM device serial", type: "text" },
  { key: "atm_latitude", label: "ATM latitude", type: "number", step: "any" },
  { key: "atm_longitude", label: "ATM longitude", type: "number", step: "any" },
];

const SITE_FIELDS: FieldSpec[] = [
  { key: "name", label: "Site name", type: "text", required: true },
  { key: "latitude", label: "Latitude", type: "number", step: "any" },
  { key: "longitude", label: "Longitude", type: "number", step: "any" },
];

const WELL_FIELDS: FieldSpec[] = [
  { key: "name", label: "Well name", type: "text", required: true },
  {
    key: "well_type",
    label: "Type",
    type: "select",
    required: true,
    options: [
      { value: "in_stream", label: "In Stream (IS)" },
      { value: "groundwater", label: "Groundwater (GW)" },
    ],
  },
  { key: "device_serial", label: "Device serial", type: "text" },
];

function renderFields(container: HTMLElement, fields: FieldSpec[], values: FieldValues): void {
  container.innerHTML = "";
  for (const field of fields) {
    const wrapper = document.createElement("label");
    wrapper.className = "field";

    const labelText = document.createElement("span");
    labelText.textContent = field.label;
    wrapper.appendChild(labelText);

    let input: HTMLInputElement | HTMLSelectElement;
    if (field.type === "select") {
      const select = document.createElement("select");
      for (const opt of field.options ?? []) {
        const optionEl = document.createElement("option");
        optionEl.value = opt.value;
        optionEl.textContent = opt.label;
        select.appendChild(optionEl);
      }
      input = select;
    } else {
      const inputEl = document.createElement("input");
      inputEl.type = field.type;
      if (field.step) inputEl.step = field.step;
      input = inputEl;
    }
    input.name = field.key;
    input.required = Boolean(field.required);

    const value = values[field.key];
    if (value !== undefined && value !== null) {
      input.value = String(value);
    }
    wrapper.appendChild(input);
    container.appendChild(wrapper);
  }
}

function readFormValues(fields: FieldSpec[], form: HTMLFormElement): FieldValues {
  const data = new FormData(form);
  const values: FieldValues = {};
  for (const field of fields) {
    const raw = data.get(field.key);
    if (field.type === "number") {
      values[field.key] = raw === null || raw === "" ? null : Number(raw);
    } else {
      values[field.key] = raw === null ? "" : String(raw);
    }
  }
  return values;
}

interface DialogOptions {
  title: string;
  fields: FieldSpec[];
  initialValues: FieldValues;
  onSubmit: (values: FieldValues) => Promise<void>;
}

function openEntityDialog(options: DialogOptions): void {
  const dialog = document.querySelector<HTMLDialogElement>("#entity-dialog")!;

  // The dialog is reused for every entity type/mode - clone-and-replace the
  // form so no listener from a previous open() call is still attached.
  const oldForm = document.querySelector<HTMLFormElement>("#entity-form")!;
  const form = oldForm.cloneNode(true) as HTMLFormElement;
  oldForm.replaceWith(form);

  form.querySelector<HTMLElement>("#entity-dialog-title")!.textContent = options.title;
  const fieldsContainer = form.querySelector<HTMLElement>("#entity-dialog-fields")!;
  renderFields(fieldsContainer, options.fields, options.initialValues);

  form.querySelector<HTMLButtonElement>("#entity-dialog-cancel")!.addEventListener("click", () => dialog.close());

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = readFormValues(options.fields, form);
    const submitButton = form.querySelector<HTMLButtonElement>("#entity-dialog-submit")!;
    submitButton.disabled = true;
    options
      .onSubmit(values)
      .then(() => dialog.close())
      .catch((err: unknown) => {
        alert(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        submitButton.disabled = false;
      });
  });

  dialog.showModal();
}

function confirmDelete(kind: string, name: string): boolean {
  return confirm(
    `Delete ${kind} "${name}"? This removes it from the app, but any logger files already downloaded for it are ` +
      "left on disk untouched.",
  );
}

// ---- Project ---------------------------------------------------------

export function openCreateProjectDialog(onDone: () => void): void {
  openEntityDialog({
    title: "Add Project",
    fields: PROJECT_FIELDS,
    initialValues: { map_zoom: 12 },
    onSubmit: async (v) => {
      await api.createProject({
        name: String(v.name),
        description: String(v.description ?? ""),
        timezone: String(v.timezone),
        map_center_lat: v.map_center_lat as number | null,
        map_center_lon: v.map_center_lon as number | null,
        map_zoom: (v.map_zoom as number) ?? 12,
      });
      onDone();
    },
  });
}

export function openEditProjectDialog(project: ProjectOut, onDone: () => void): void {
  openEntityDialog({
    title: `Edit Project: ${project.name}`,
    fields: PROJECT_FIELDS,
    initialValues: {
      name: project.name,
      description: project.description,
      timezone: project.timezone,
      map_center_lat: project.map_center_lat,
      map_center_lon: project.map_center_lon,
      map_zoom: project.map_zoom,
    },
    onSubmit: async (v) => {
      await api.updateProject(project.id, {
        name: String(v.name),
        description: String(v.description ?? ""),
        timezone: String(v.timezone),
        map_center_lat: v.map_center_lat as number | null,
        map_center_lon: v.map_center_lon as number | null,
        map_zoom: (v.map_zoom as number) ?? 12,
      });
      onDone();
    },
  });
}

export async function confirmAndDeleteProject(project: ProjectOut, onDone: () => void): Promise<void> {
  if (!confirmDelete("project", project.name)) return;
  await api.deleteProject(project.id);
  onDone();
}

// ---- Reach --------------------------------------------------------------

export function openCreateReachDialog(projectId: string, onDone: () => void): void {
  openEntityDialog({
    title: "Add Reach",
    fields: REACH_FIELDS,
    initialValues: {},
    onSubmit: async (v) => {
      await api.createReach(projectId, {
        name: String(v.name),
        atm_name: String(v.atm_name),
        atm_device_serial: v.atm_device_serial ? String(v.atm_device_serial) : null,
        atm_latitude: v.atm_latitude as number | null,
        atm_longitude: v.atm_longitude as number | null,
      });
      onDone();
    },
  });
}

export function openEditReachDialog(reach: ReachOut, onDone: () => void): void {
  openEntityDialog({
    title: `Edit Reach: ${reach.name}`,
    fields: REACH_FIELDS,
    initialValues: {
      name: reach.name,
      atm_name: reach.atm_well.name,
      atm_device_serial: reach.atm_well.device_serial,
      atm_latitude: reach.atm_well.latitude,
      atm_longitude: reach.atm_well.longitude,
    },
    onSubmit: async (v) => {
      await api.updateReach(reach.id, {
        name: String(v.name),
        atm_name: String(v.atm_name),
        atm_device_serial: v.atm_device_serial ? String(v.atm_device_serial) : null,
        atm_latitude: v.atm_latitude as number | null,
        atm_longitude: v.atm_longitude as number | null,
      });
      onDone();
    },
  });
}

export async function confirmAndDeleteReach(reach: ReachOut, onDone: () => void): Promise<void> {
  if (!confirmDelete("reach", reach.name)) return;
  await api.deleteReach(reach.id);
  onDone();
}

// ---- Site -----------------------------------------------------------------

export function openCreateSiteDialog(reachId: string, onDone: () => void): void {
  openEntityDialog({
    title: "Add Site",
    fields: SITE_FIELDS,
    initialValues: {},
    onSubmit: async (v) => {
      await api.createSite(reachId, {
        name: String(v.name),
        latitude: v.latitude as number | null,
        longitude: v.longitude as number | null,
      });
      onDone();
    },
  });
}

export function openEditSiteDialog(site: SiteOut, onDone: () => void): void {
  openEntityDialog({
    title: `Edit Site: ${site.name}`,
    fields: SITE_FIELDS,
    initialValues: { name: site.name, latitude: site.latitude, longitude: site.longitude },
    onSubmit: async (v) => {
      await api.updateSite(site.id, {
        name: String(v.name),
        latitude: v.latitude as number | null,
        longitude: v.longitude as number | null,
      });
      onDone();
    },
  });
}

export async function confirmAndDeleteSite(site: SiteOut, onDone: () => void): Promise<void> {
  if (!confirmDelete("site", site.name)) return;
  await api.deleteSite(site.id);
  onDone();
}

// ---- Well -----------------------------------------------------------------

export function openCreateWellDialog(siteId: string, onDone: () => void): void {
  openEntityDialog({
    title: "Add Well",
    fields: WELL_FIELDS,
    initialValues: { well_type: "groundwater" },
    onSubmit: async (v) => {
      await api.createWell(siteId, {
        name: String(v.name),
        well_type: String(v.well_type),
        device_serial: v.device_serial ? String(v.device_serial) : null,
      });
      onDone();
    },
  });
}

export function openEditWellDialog(well: WellOut, onDone: () => void): void {
  openEntityDialog({
    title: `Edit Well: ${well.name}`,
    fields: WELL_FIELDS,
    initialValues: { name: well.name, well_type: well.well_type, device_serial: well.device_serial },
    onSubmit: async (v) => {
      await api.updateWell(well.id, {
        name: String(v.name),
        well_type: String(v.well_type),
        device_serial: v.device_serial ? String(v.device_serial) : null,
      });
      onDone();
    },
  });
}

export async function confirmAndDeleteWell(well: WellOut, onDone: () => void): Promise<void> {
  if (!confirmDelete("well", well.name)) return;
  await api.deleteWell(well.id);
  onDone();
}
