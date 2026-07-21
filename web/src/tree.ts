import type { ProjectOut, ReachOut } from "./types";

// Renders the Project > Reach > Site tree (Implementation Plan.md section 12).
// Clicking a Reach is the only interactive behavior in Phase 4 - selecting it
// re-centers the map. Sites are listed for context but aren't clickable yet
// (that's the Phase 6 detail view, not built here).
export function renderTree(container: HTMLElement, projects: ProjectOut[], onSelectReach: (reach: ReachOut) => void): void {
  container.innerHTML = "";
  const root = document.createElement("ul");
  root.className = "tree";

  for (const project of projects) {
    root.appendChild(renderProjectNode(project, container, onSelectReach));
  }

  container.appendChild(root);
}

function renderProjectNode(project: ProjectOut, treeRoot: HTMLElement, onSelectReach: (reach: ReachOut) => void): HTMLElement {
  const item = document.createElement("li");

  const label = document.createElement("div");
  label.className = "tree-label tree-label-project";
  label.textContent = project.name;
  item.appendChild(label);

  const reachList = document.createElement("ul");
  for (const reach of project.reaches) {
    reachList.appendChild(renderReachNode(reach, treeRoot, onSelectReach));
  }
  item.appendChild(reachList);

  return item;
}

function renderReachNode(reach: ReachOut, treeRoot: HTMLElement, onSelectReach: (reach: ReachOut) => void): HTMLElement {
  const item = document.createElement("li");

  const label = document.createElement("div");
  label.className = "tree-label tree-label-reach";
  label.textContent = reach.name;
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
  item.appendChild(label);

  const siteList = document.createElement("ul");
  for (const site of reach.sites) {
    const siteItem = document.createElement("li");
    const siteLabel = document.createElement("div");
    siteLabel.className = "tree-label tree-label-site";
    siteLabel.textContent = site.name;
    siteItem.appendChild(siteLabel);
    siteList.appendChild(siteItem);
  }
  item.appendChild(siteList);

  return item;
}
