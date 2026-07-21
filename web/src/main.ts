import "./style.css";

import { fetchProjects } from "./api";
import * as mgmt from "./management";
import { SiteMap } from "./map";
import { renderTree } from "./tree";
import type { ProjectOut, ReachOut } from "./types";

let selectedReachId: string | null = null;
let siteMap: SiteMap;

async function main(): Promise<void> {
  const mapContainer = document.querySelector<HTMLElement>("#map")!;
  const emptyStateEl = document.querySelector<HTMLElement>("#map-empty-state")!;
  siteMap = new SiteMap(mapContainer, emptyStateEl);

  document.querySelector<HTMLButtonElement>("#add-project-button")!.addEventListener("click", () => {
    mgmt.openCreateProjectDialog(refresh);
  });

  await refresh();
}

async function refresh(): Promise<void> {
  const treeContainer = document.querySelector<HTMLElement>("#tree")!;
  const errorEl = document.querySelector<HTMLElement>("#error-banner")!;

  try {
    const projects = await fetchProjects();
    renderTree(
      treeContainer,
      projects,
      (reach: ReachOut) => {
        selectedReachId = reach.id;
        siteMap.showReach(reach).catch((err: unknown) => showError(errorEl, err));
      },
      () => {
        refresh().catch((err: unknown) => showError(errorEl, err));
      },
    );
    errorEl.hidden = true;

    const reachToShow = findReach(projects, selectedReachId) ?? projects[0]?.reaches[0];
    if (reachToShow) {
      selectedReachId = reachToShow.id;
      await siteMap.showReach(reachToShow);
      treeContainer.querySelector<HTMLElement>(`.tree-label-reach[data-reach-id="${cssEscape(reachToShow.id)}"]`)?.classList.add(
        "selected",
      );
    }
  } catch (err) {
    showError(errorEl, err);
  }
}

function findReach(projects: ProjectOut[], reachId: string | null): ReachOut | undefined {
  if (reachId === null) return undefined;
  for (const project of projects) {
    const found = project.reaches.find((r) => r.id === reachId);
    if (found) return found;
  }
  return undefined;
}

function cssEscape(value: string): string {
  return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : value.replace(/["\\]/g, "\\$&");
}

function showError(el: HTMLElement, err: unknown): void {
  console.error(err);
  el.textContent = `Failed to load data: ${err instanceof Error ? err.message : String(err)}`;
  el.hidden = false;
}

main();
