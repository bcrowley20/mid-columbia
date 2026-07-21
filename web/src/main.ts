import "./style.css";

import { fetchProjects } from "./api";
import { SiteMap } from "./map";
import { renderTree } from "./tree";
import type { ReachOut } from "./types";

async function main(): Promise<void> {
  const treeContainer = document.querySelector<HTMLElement>("#tree")!;
  const mapContainer = document.querySelector<HTMLElement>("#map")!;
  const emptyStateEl = document.querySelector<HTMLElement>("#map-empty-state")!;
  const errorEl = document.querySelector<HTMLElement>("#error-banner")!;

  const siteMap = new SiteMap(mapContainer, emptyStateEl);

  try {
    const projects = await fetchProjects();
    renderTree(treeContainer, projects, (reach: ReachOut) => {
      siteMap.showReach(reach).catch((err: unknown) => showError(errorEl, err));
    });

    // Show the first reach by default so the map isn't blank on first load.
    const firstReach = projects[0]?.reaches[0];
    if (firstReach) {
      await siteMap.showReach(firstReach);
      treeContainer.querySelector<HTMLElement>(".tree-label-reach")?.classList.add("selected");
    }
  } catch (err) {
    showError(errorEl, err);
  }
}

function showError(el: HTMLElement, err: unknown): void {
  console.error(err);
  el.textContent = `Failed to load data: ${err instanceof Error ? err.message : String(err)}`;
  el.hidden = false;
}

main();
