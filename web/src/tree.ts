import * as mgmt from "./management";
import type { ProjectOut, ReachOut, SiteOut, WellOut } from "./types";

// Renders the Project > Reach > Site > Well tree (Implementation Plan.md
// section 12) plus the Phase 5 add/edit/delete controls at every level.
// Clicking a Reach re-centers the map (Phase 4). Clicking a Site opens the
// Phase 6 chart panel.
export function renderTree(
  container: HTMLElement,
  projects: ProjectOut[],
  onSelectReach: (reach: ReachOut) => void,
  onSelectSite: (reach: ReachOut, site: SiteOut) => void,
  onDataChanged: () => void,
): void {
  container.innerHTML = "";
  const root = document.createElement("ul");
  root.className = "tree";

  for (const project of projects) {
    root.appendChild(renderProjectNode(project, container, onSelectReach, onSelectSite, onDataChanged));
  }

  container.appendChild(root);
}

function renderRow(labelText: string, labelClass: string): { item: HTMLElement; label: HTMLElement; actions: HTMLElement } {
  const item = document.createElement("li");
  const row = document.createElement("div");
  row.className = "tree-row";

  const label = document.createElement("div");
  label.className = `tree-label ${labelClass}`;
  label.textContent = labelText;
  row.appendChild(label);

  const actions = document.createElement("span");
  actions.className = "tree-actions";
  row.appendChild(actions);

  item.appendChild(row);
  return { item, label, actions };
}

function addActionButton(actions: HTMLElement, text: string, title: string, onClick: () => void): void {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-action";
  button.textContent = text;
  button.title = title;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  actions.appendChild(button);
}

function renderProjectNode(
  project: ProjectOut,
  treeRoot: HTMLElement,
  onSelectReach: (reach: ReachOut) => void,
  onSelectSite: (reach: ReachOut, site: SiteOut) => void,
  onDataChanged: () => void,
): HTMLElement {
  const { item, actions } = renderRow(project.name, "tree-label-project");

  addActionButton(actions, "+ Reach", "Add a reach to this project", () =>
    mgmt.openCreateReachDialog(project.id, onDataChanged),
  );
  addActionButton(actions, "Edit", "Edit this project", () => mgmt.openEditProjectDialog(project, onDataChanged));
  addActionButton(actions, "Delete", "Delete this project", () => {
    mgmt.confirmAndDeleteProject(project, onDataChanged).catch((err: unknown) => alert(String(err)));
  });

  const reachList = document.createElement("ul");
  for (const reach of project.reaches) {
    reachList.appendChild(renderReachNode(reach, treeRoot, onSelectReach, onSelectSite, onDataChanged));
  }
  item.appendChild(reachList);

  return item;
}

function renderReachNode(
  reach: ReachOut,
  treeRoot: HTMLElement,
  onSelectReach: (reach: ReachOut) => void,
  onSelectSite: (reach: ReachOut, site: SiteOut) => void,
  onDataChanged: () => void,
): HTMLElement {
  const { item, label, actions } = renderRow(reach.name, "tree-label-reach");
  label.tabIndex = 0;
  label.dataset.reachId = reach.id;

  const select = () => {
    for (const el of treeRoot.querySelectorAll<HTMLElement>(".tree-label-reach.selected")) {
      el.classList.remove("selected");
    }
    label.classList.add("selected");
    onSelectReach(reach);
  };
  label.addEventListener("click", select);
  label.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  });

  addActionButton(actions, "+ Site", "Add a site to this reach", () => mgmt.openCreateSiteDialog(reach.id, onDataChanged));
  addActionButton(actions, "Edit", "Edit this reach and its ATM well", () => mgmt.openEditReachDialog(reach, onDataChanged));
  addActionButton(actions, "Delete", "Delete this reach", () => {
    mgmt.confirmAndDeleteReach(reach, onDataChanged).catch((err: unknown) => alert(String(err)));
  });

  const siteList = document.createElement("ul");
  for (const site of reach.sites) {
    siteList.appendChild(renderSiteNode(reach, site, onSelectSite, onDataChanged));
  }
  item.appendChild(siteList);

  return item;
}

function renderSiteNode(
  reach: ReachOut,
  site: SiteOut,
  onSelectSite: (reach: ReachOut, site: SiteOut) => void,
  onDataChanged: () => void,
): HTMLElement {
  const { item, label, actions } = renderRow(site.name, "tree-label-site");
  label.tabIndex = 0;

  const select = () => onSelectSite(reach, site);
  label.addEventListener("click", select);
  label.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  });

  addActionButton(actions, "+ Well", "Add a well to this site", () => mgmt.openCreateWellDialog(site.id, onDataChanged));
  addActionButton(actions, "Edit", "Edit this site", () => mgmt.openEditSiteDialog(site, onDataChanged));
  addActionButton(actions, "Delete", "Delete this site", () => {
    mgmt.confirmAndDeleteSite(site, onDataChanged).catch((err: unknown) => alert(String(err)));
  });

  const wellList = document.createElement("ul");
  for (const well of site.wells) {
    wellList.appendChild(renderWellNode(well, onDataChanged));
  }
  item.appendChild(wellList);

  return item;
}

function renderWellNode(well: WellOut, onDataChanged: () => void): HTMLElement {
  const { item, actions } = renderRow(`${well.name} (${well.well_type === "in_stream" ? "IS" : "GW"})`, "tree-label-well");

  addActionButton(actions, "Edit", "Edit this well", () => mgmt.openEditWellDialog(well, onDataChanged));
  addActionButton(actions, "Delete", "Delete this well", () => {
    mgmt.confirmAndDeleteWell(well, onDataChanged).catch((err: unknown) => alert(String(err)));
  });

  return item;
}
