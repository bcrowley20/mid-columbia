import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { fetchWellReadings } from "./api";
import type { ReachOut, SiteOut, WellOut } from "./types";

// Phase 6 detail view. Opens as a bottom panel over the map (not a separate
// browser window - see Implementation Plan.md section 6's design discussion)
// when a Site is clicked, plotting every well at that site plus the reach's
// ATM well as an optional overlay.

const HOUR_SECONDS = 3600;

// Base color per well type - index 0 is what a site with only one well of
// that type gets (matches the blue/red site/ATM marker convention already
// used on the map, map.ts). A site with more than one well of the same type
// (e.g. Carlson's Site 3, two GW wells) cycles through the rest of the array
// for that well's depth *and* temperature lines - deliberately spread across
// dark/base/light rather than adjacent mid-tones (the original three blues
// here were all close enough in lightness that two wells still read as "the
// same blue" at a glance, per the user - these are chosen to be told apart
// without needing the legend.
const GW_SHADES = ["#2563eb", "#1e3a8a", "#38bdf8"]; // royal blue, navy, sky
const IS_SHADES = ["#059669", "#14532d", "#4ade80"]; // emerald, forest, light green
const ATM_COLOR = "#dc2626";

// Export image layout (ChartPanel.exportImage) - a fixed 16:9 canvas sized
// for dropping straight into a slide/document, independent of whatever
// aspect ratio the live bottom panel happens to be on screen. Rendered at
// EXPORT_SCALE for a crisp image at presentation size, not just a 1:1 grab
// of the (much smaller) on-screen chart.
const EXPORT_WIDTH = 1600;
const EXPORT_HEIGHT = 900;
const EXPORT_SCALE = 2;
const EXPORT_PAD = 24;
const EXPORT_LOGO_HEIGHT = 56;
const EXPORT_LEGEND_ROW_HEIGHT = 26;
const EXPORT_LEGEND_SWATCH_WIDTH = 28;
const EXPORT_LEGEND_ITEM_GAP = 24;
const EXPORT_FONT = "13px system-ui, -apple-system, sans-serif";
const EXPORT_TEXT_COLOR = "#1e293b"; // --color-text

// The File System Access API (window.showSaveFilePicker) is what actually
// pops the native "choose a save location" dialog the user asked for -
// Chromium-only as of writing, not yet in TypeScript's bundled DOM lib.
// FileSystemFileHandle/FileSystemWritableFileStream are already declared
// there (used elsewhere for drag-and-drop), so only the entry point itself
// needs augmenting.
declare global {
  interface Window {
    showSaveFilePicker?: (options: {
      suggestedName?: string;
      types?: { description?: string; accept: Record<string, string[]> }[];
    }) => Promise<FileSystemFileHandle>;
  }
}

interface SeriesSpec {
  label: string;
  color: string;
  scale: "depth" | "temp" | "pressure";
  dash?: number[];
  show: boolean;
  // Raw (timestamp epoch seconds -> value) points, before alignment to the
  // shared chart-wide time grid.
  points: Map<number, number>;
  unit: string;
  // Explicit source flag - render()'s water-vs-air series classification
  // used to test `color === ATM_COLOR`, which broke once year mode (below)
  // repurposes color to mean "which year" instead of "which source."
  isAtm?: boolean;
}

// Year-over-year comparison (ChartPanel.selectedYears). Deliberately picked
// from outside blue/green/red - this app already uses those for well
// identity elsewhere (GW_SHADES, IS_SHADES, ATM_COLOR, and the map markers
// in map.ts), so a year-colored line reusing one of those hues could read
// as "this is an in-stream well" instead of "this is 2025." Validated with
// the dataviz skill's validate_palette.js (light mode; this app has no dark
// theme to validate against) - ALL CHECKS PASS on this order (orange,
// violet, aqua, magenta, yellow); the CVD-separation and surface-contrast
// checks land in the skill's documented "WARN but legal with visible
// labels" band, which this chart already satisfies via its always-on
// legend and per-series hover tips.
const YEAR_COLORS = ["#eb6834", "#4a3aa7", "#1baf7a", "#e87ba4", "#eda100"];
// A leap year, so a real leap year's Feb 29 always has a slot to remap onto
// (a non-leap real year just never produces a point there - no special
// casing needed).
const YEAR_REFERENCE = 2000;
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Left-to-right precedence for the non-x axes actually present in a given
// render() call - a chart never declares an axis for a scale nobody plotted
// (e.g. the ATM-only view from openAtm() has no "depth" series at all, so no
// empty "Depth" axis should reserve space for it).
const AXIS_SPECS: { scale: SeriesSpec["scale"]; side: 1 | 3; label: string; defaultUnit: string }[] = [
  { scale: "depth", side: 3, label: "Depth", defaultUnit: "ft" },
  { scale: "temp", side: 1, label: "Temperature", defaultUnit: "°F" },
  { scale: "pressure", side: 1, label: "Pressure", defaultUnit: "kPa" },
];

export class ChartPanel {
  private readonly panel: HTMLElement;
  private readonly titleEl: HTMLElement;
  private readonly legendEl: HTMLElement;
  private readonly bodyEl: HTMLElement;
  private readonly togglesEl: HTMLElement;
  private readonly waterTempCheckbox: HTMLInputElement;
  private readonly waterPressureCheckbox: HTMLInputElement;
  private readonly airTempCheckbox: HTMLInputElement;
  private readonly airPressureCheckbox: HTMLInputElement;
  private readonly yearRowEl: HTMLElement;
  private readonly yearTogglesEl: HTMLElement;
  private plot: uPlot | null = null;
  private fullRange: { min: number; max: number } | null = null;
  private waterTempIndices: number[] = [];
  private waterPressureIndices: number[] = [];
  private airTempIndex: number | null = null;
  private airPressureIndex: number | null = null;
  // Raw, un-year-expanded specs from the last fetch - what a year toggle
  // re-renders from (no re-fetch needed). Distinct from renderedSpecs below.
  private currentSpecs: SeriesSpec[] = [];
  // The specs actually plotted in `this.plot` right now - identical to
  // currentSpecs when no year is selected, otherwise the year-expanded (base
  // spec x selected year) list. 1:1 aligned with `this.plot.series[1..]`,
  // which exportImage() needs (see renderExportImage).
  private renderedSpecs: SeriesSpec[] = [];
  private availableYears: number[] = [];
  private selectedYears: Set<number> = new Set();
  private logoImagePromise: Promise<HTMLImageElement> | null = null;

  constructor() {
    this.panel = document.querySelector<HTMLElement>("#chart-panel")!;
    this.titleEl = document.querySelector<HTMLElement>("#chart-panel-title")!;
    this.legendEl = document.querySelector<HTMLElement>("#chart-panel-legend")!;
    this.bodyEl = document.querySelector<HTMLElement>("#chart-panel-body")!;
    this.togglesEl = document.querySelector<HTMLElement>("#chart-panel-toggles")!;
    this.waterTempCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-water-temp")!;
    this.waterPressureCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-water-pressure")!;
    this.airTempCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-air-temp")!;
    this.airPressureCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-air-pressure")!;
    this.yearRowEl = document.querySelector<HTMLElement>("#chart-panel-year-row")!;
    this.yearTogglesEl = document.querySelector<HTMLElement>("#chart-panel-year-toggles")!;

    document.querySelector<HTMLButtonElement>("#chart-panel-close")!.addEventListener("click", () => this.close());
    document.querySelector<HTMLButtonElement>("#chart-export")!.addEventListener("click", () => this.exportImage());
    this.waterTempCheckbox.addEventListener("change", () => {
      for (const idx of this.waterTempIndices) this.plot?.setSeries(idx, { show: this.waterTempCheckbox.checked });
    });
    this.waterPressureCheckbox.addEventListener("change", () => {
      for (const idx of this.waterPressureIndices) {
        this.plot?.setSeries(idx, { show: this.waterPressureCheckbox.checked });
      }
    });
    this.airTempCheckbox.addEventListener("change", () => {
      if (this.airTempIndex !== null) this.plot?.setSeries(this.airTempIndex, { show: this.airTempCheckbox.checked });
    });
    this.airPressureCheckbox.addEventListener("change", () => {
      if (this.airPressureIndex !== null) {
        this.plot?.setSeries(this.airPressureIndex, { show: this.airPressureCheckbox.checked });
      }
    });
    window.addEventListener("resize", () => this.resize());
  }

  close(): void {
    this.panel.hidden = true;
    this.plot?.destroy();
    this.plot = null;
    this.currentSpecs = [];
    this.renderedSpecs = [];
    this.availableYears = [];
    this.selectedYears = new Set();
    this.renderYearToggles();
  }

  async open(reach: ReachOut, site: SiteOut): Promise<void> {
    this.panel.hidden = false;
    this.titleEl.textContent = `${reach.name} › ${site.name}`;
    this.togglesEl.hidden = false;
    this.waterTempCheckbox.checked = false;
    this.waterPressureCheckbox.checked = false;
    this.airTempCheckbox.checked = false;
    this.airPressureCheckbox.checked = false;
    this.selectedYears = new Set();
    this.bodyEl.innerHTML = `<div id="chart-panel-empty">Loading…</div>`;

    try {
      const gwCount = { n: 0 };
      const isCount = { n: 0 };
      const wellSeries = await Promise.all(
        site.wells.map(async (well) => {
          const isGw = well.well_type === "groundwater";
          const shade = isGw ? GW_SHADES[gwCount.n % GW_SHADES.length] : IS_SHADES[isCount.n % IS_SHADES.length];
          if (isGw) gwCount.n++;
          else isCount.n++;
          return this.fetchWellSeries(well, shade);
        }),
      );

      const atmSeries = await this.fetchAtmSeries(reach.atm_well);

      const depthSeries = wellSeries.map((w) => w.depth).filter((s) => s.points.size > 0);
      const tempSeries = [...wellSeries.map((w) => w.temp), ...(atmSeries.temp ? [atmSeries.temp] : [])].filter(
        (s) => s.points.size > 0,
      );
      const pressureSeries = [...wellSeries.map((w) => w.pressure), ...(atmSeries.pressure ? [atmSeries.pressure] : [])].filter(
        (s) => s.points.size > 0,
      );

      if (depthSeries.length === 0 && tempSeries.length === 0 && pressureSeries.length === 0) {
        this.availableYears = [];
        this.renderYearToggles();
        this.bodyEl.innerHTML = `<div id="chart-panel-empty">No readings for this site's wells yet.</div>`;
        return;
      }

      const allSpecs = [...depthSeries, ...tempSeries, ...pressureSeries];
      this.availableYears = computeAvailableYears(allSpecs);
      this.renderYearToggles();
      this.render(allSpecs);
    } catch (err) {
      this.availableYears = [];
      this.renderYearToggles();
      this.showLoadError(err);
    }
  }

  // Reach.atm_well isn't part of any Site, so it has no chart entry point
  // through open() above - clicking its own marker on the map (map.ts) is
  // the only way in. There's no depth (no paired well to derive it from) and
  // only ever these two raw series, so both are shown by default rather than
  // gated behind the site view's toggles - see the togglesEl.hidden below.
  async openAtm(reach: ReachOut, atmWell: WellOut): Promise<void> {
    this.panel.hidden = false;
    this.titleEl.textContent = `${reach.name} › ${atmWell.name} (atmospheric reference)`;
    this.togglesEl.hidden = true;
    this.selectedYears = new Set();
    this.bodyEl.innerHTML = `<div id="chart-panel-empty">Loading…</div>`;

    try {
      const [tempResult, pressureResult] = await Promise.all([
        fetchWellReadings(atmWell.id, "air_temperature"),
        fetchWellReadings(atmWell.id, "air_pressure"),
      ]);

      const specs: SeriesSpec[] = [];
      if (tempResult.points.length > 0) {
        specs.push({
          label: `${atmWell.name} air temp`,
          color: ATM_COLOR,
          scale: "temp",
          show: true,
          unit: tempResult.points[0]?.unit ?? "°F",
          points: toPointMap(tempResult.points),
          isAtm: true,
        });
      }
      if (pressureResult.points.length > 0) {
        specs.push({
          label: `${atmWell.name} air pressure`,
          color: ATM_COLOR,
          scale: "pressure",
          dash: [6, 4],
          show: true,
          unit: pressureResult.points[0]?.unit ?? "kPa",
          points: toPointMap(pressureResult.points),
          isAtm: true,
        });
      }

      if (specs.length === 0) {
        this.availableYears = [];
        this.renderYearToggles();
        this.bodyEl.innerHTML = `<div id="chart-panel-empty">No readings for this atmospheric well yet.</div>`;
        return;
      }

      this.availableYears = computeAvailableYears(specs);
      this.renderYearToggles();
      this.render(specs);
    } catch (err) {
      this.availableYears = [];
      this.renderYearToggles();
      this.showLoadError(err);
    }
  }

  // open()/openAtm() previously left the panel stuck on "Loading…" forever
  // if a fetch failed - the rejection only ever reached console.error via
  // main.ts's onSelectSite/onSelectAtm, with no visible feedback in the UI
  // itself (Phase 7 error-handling audit, Implementation Plan.md section 14).
  private showLoadError(err: unknown): void {
    console.error(err);
    const el = document.createElement("div");
    el.id = "chart-panel-empty";
    el.textContent = `Failed to load chart data: ${err instanceof Error ? err.message : String(err)}`;
    this.bodyEl.innerHTML = "";
    this.bodyEl.appendChild(el);
  }

  private async fetchWellSeries(
    well: WellOut,
    color: string,
  ): Promise<{ depth: SeriesSpec; temp: SeriesSpec; pressure: SeriesSpec }> {
    const [depthResult, tempResult, pressureResult] = await Promise.all([
      fetchWellReadings(well.id, "water_depth"),
      fetchWellReadings(well.id, "water_temperature"),
      fetchWellReadings(well.id, "water_pressure"),
    ]);
    return {
      depth: {
        label: `${well.name} depth`,
        color,
        scale: "depth",
        show: true,
        unit: depthResult.points[0]?.unit ?? "ft",
        points: toPointMap(depthResult.points),
      },
      temp: {
        label: `${well.name} water temp`,
        color,
        scale: "temp",
        dash: [6, 4],
        show: false,
        unit: tempResult.points[0]?.unit ?? "°F",
        points: toPointMap(tempResult.points),
      },
      pressure: {
        label: `${well.name} pressure`,
        color,
        scale: "pressure",
        dash: [2, 2],
        show: false,
        unit: pressureResult.points[0]?.unit ?? "kPa",
        points: toPointMap(pressureResult.points),
      },
    };
  }

  private async fetchAtmSeries(
    atmWell: WellOut,
  ): Promise<{ temp: SeriesSpec | null; pressure: SeriesSpec | null }> {
    const [tempResult, pressureResult] = await Promise.all([
      fetchWellReadings(atmWell.id, "air_temperature"),
      fetchWellReadings(atmWell.id, "air_pressure"),
    ]);
    return {
      temp:
        tempResult.points.length === 0
          ? null
          : {
              label: `${atmWell.name} air temp`,
              color: ATM_COLOR,
              scale: "temp",
              dash: [2, 3],
              show: false,
              unit: tempResult.points[0]?.unit ?? "°F",
              points: toPointMap(tempResult.points),
              isAtm: true,
            },
      pressure:
        pressureResult.points.length === 0
          ? null
          : {
              label: `${atmWell.name} air pressure`,
              color: ATM_COLOR,
              scale: "pressure",
              dash: [2, 3],
              show: false,
              unit: pressureResult.points[0]?.unit ?? "kPa",
              points: toPointMap(pressureResult.points),
              isAtm: true,
            },
    };
  }

  // Expands each base spec into one derived spec per selected year (color
  // repurposed to mean "which year", see YEAR_COLORS) - or returns baseSpecs
  // unchanged when no year is selected, which is what makes "deselect every
  // year" a plain no-op back to the normal continuous-timeline view.
  private buildEffectiveSpecs(baseSpecs: SeriesSpec[]): SeriesSpec[] {
    if (this.selectedYears.size === 0) return baseSpecs;

    const years = [...this.selectedYears].sort((a, b) => a - b);
    const effective: SeriesSpec[] = [];
    for (const base of baseSpecs) {
      for (const year of years) {
        const points = remapPointsToYear(base.points, year);
        if (points.size === 0) continue; // this base series has no data in that year
        effective.push({ ...base, label: `${base.label} ${year}`, color: this.colorForYear(year), points });
      }
    }
    return effective;
  }

  // Color follows the year itself (its position in availableYears), never
  // selection order - so a given year is always the same color whether it's
  // the only one checked or the third one checked.
  private colorForYear(year: number): string {
    const index = this.availableYears.indexOf(year);
    return YEAR_COLORS[(index < 0 ? 0 : index) % YEAR_COLORS.length];
  }

  // Rebuilds the "Compare years" toggle row from this.availableYears - has
  // to happen in JS (unlike the static water-temp/pressure checkboxes
  // already in index.html) since the set of years is data-dependent.
  private renderYearToggles(): void {
    this.yearTogglesEl.innerHTML = "";
    this.yearRowEl.hidden = this.availableYears.length === 0;

    for (const year of this.availableYears) {
      const label = document.createElement("label");
      label.className = "chart-year-toggle";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = this.selectedYears.has(year);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) this.selectedYears.add(year);
        else this.selectedYears.delete(year);
        this.render(this.currentSpecs); // re-render from already-fetched data, no re-fetch
      });

      const swatch = document.createElement("span");
      swatch.className = "chart-year-swatch";
      swatch.style.background = this.colorForYear(year);

      label.append(checkbox, swatch, document.createTextNode(String(year)));
      this.yearTogglesEl.appendChild(label);
    }
  }

  // Shared by the live interactive render() below and by renderChartBitmap()
  // (the export path's own fresh off-screen instance) - both need the exact
  // same series/scales/axes/data shape, only their surrounding uPlot options
  // (cursor, legend, hooks, container size) differ.
  private buildChartLayout(specs: SeriesSpec[]): {
    series: uPlot.Series[];
    scales: uPlot.Scales;
    axes: uPlot.Axis[];
    data: uPlot.AlignedData;
    xMin: number;
    xMax: number;
  } {
    // uPlot requires every series to share one x-axis array. Loggers don't
    // necessarily sample on the exact same second, so timestamps are snapped
    // to the nearest hour to build one shared grid - the "interpolat[ion] for
    // display" the sponsor's brief called for. A null on that grid means this
    // series genuinely has no reading in that hour (rendered as a gap, not
    // bridged - spanGaps stays false), not a placeholder for another series.
    const grid = buildHourGrid(specs);
    const xs = grid.map((t) => t);
    const data: (number | null)[][] = [xs, ...specs.map((spec) => grid.map((t) => spec.points.get(t) ?? null))];

    // Only declare an axis (and its scale) for a series kind actually present
    // in this render - e.g. openAtm()'s chart has no "depth" series at all,
    // so no empty Depth axis should reserve space on the left.
    const presentAxes = AXIS_SPECS.filter((a) => specs.some((s) => s.scale === a.scale));

    const series: uPlot.Series[] = [
      {},
      ...specs.map((spec) => ({
        label: spec.label,
        scale: spec.scale,
        stroke: spec.color,
        width: spec.scale === "depth" ? 2 : 1.5,
        dash: spec.dash,
        show: spec.show,
        points: { show: false },
      })),
    ];

    // Year mode (this.selectedYears non-empty) locks the x-axis to the full
    // Jan-Dec span of the synthetic YEAR_REFERENCE, regardless of how much
    // of it the selected years' data actually covers, and drops the (fake)
    // year from tick labels - read here rather than threaded through as a
    // parameter since both render() (live chart) and renderChartBitmap()
    // (export) share this method and both need the same behavior.
    const yearMode = this.selectedYears.size > 0;
    const yearRange: [number, number] = [
      Date.UTC(YEAR_REFERENCE, 0, 1) / 1000,
      Date.UTC(YEAR_REFERENCE, 11, 31, 23, 59, 59) / 1000,
    ];

    const scales: uPlot.Scales = {
      x: yearMode ? { time: true, range: yearRange } : { time: true },
      ...Object.fromEntries(presentAxes.map((a) => [a.scale, {}])),
    };

    // The first present axis (normally depth, on the left) keeps uPlot's
    // default gridlines; any additional right-side axis stacked next to it
    // (temp, pressure) suppresses its own grid so the two don't overlay
    // each other at different scales.
    const axes: uPlot.Axis[] = [
      yearMode ? { values: formatMonthOnlyAxisValues } : {},
      ...presentAxes.map((a, i) => ({
        scale: a.scale,
        label: `${a.label} (${specs.find((s) => s.scale === a.scale)?.unit ?? a.defaultUnit})`,
        side: a.side,
        grid: i === 0 ? undefined : { show: false },
      })),
    ];

    return {
      series,
      scales,
      axes,
      data: data as uPlot.AlignedData,
      xMin: yearMode ? yearRange[0] : xs[0],
      xMax: yearMode ? yearRange[1] : xs[xs.length - 1],
    };
  }

  private render(baseSpecs: SeriesSpec[]): void {
    // destroy() also detaches the legend even though it's mounted outside
    // `bodyEl` (into the header's #chart-panel-legend) - needed here since
    // open() can be called again, for a different site, without close() ever
    // running first (clicking straight from one site's marker to another's).
    this.plot?.destroy();
    this.plot = null;
    this.bodyEl.innerHTML = "";
    this.currentSpecs = baseSpecs;

    // Year toggle changes call render(this.currentSpecs) again - re-deriving
    // the year-expanded list fresh each time from the raw base specs rather
    // than re-fetching. Identical to baseSpecs when no year is selected.
    const specs = this.buildEffectiveSpecs(baseSpecs);
    this.renderedSpecs = specs;

    this.waterTempIndices = [];
    this.waterPressureIndices = [];
    this.airTempIndex = null;
    this.airPressureIndex = null;
    specs.forEach((spec, i) => {
      const seriesIdx = i + 1;
      if (spec.scale === "temp") {
        if (spec.isAtm) this.airTempIndex = seriesIdx;
        else this.waterTempIndices.push(seriesIdx);
      } else if (spec.scale === "pressure") {
        if (spec.isAtm) this.airPressureIndex = seriesIdx;
        else this.waterPressureIndices.push(seriesIdx);
      }
    });

    const { series, scales, axes, data, xMin, xMax } = this.buildChartLayout(specs);
    this.fullRange = { min: xMin, max: xMax };

    // One small floating label per series, positioned exactly at that
    // series' own point for the hovered timestamp (`setCursor` hook below) -
    // "little tips at each of the intersection points," per the user, rather
    // than one aggregate box or relying on uPlot's default legend values
    // (which only ever live in the header now, disconnected from where the
    // cursor actually is on the chart).
    const tipEls = specs.map((spec) => this.createTipEl(spec.color));

    const { width, height } = this.chartSize();
    const opts: uPlot.Options = {
      width,
      height,
      series,
      scales,
      axes,
      cursor: {
        drag: { x: true, y: false, uni: 20 },
      },
      // `live: false` drops the per-hover value column (uPlot's default
      // "u-inline" legend already lays series out as compact horizontal
      // chips - it's the value column that would need real width). `mount`
      // moves the resulting table into the header strip between the site
      // name and the temperature checkboxes, instead of uPlot's default of
      // appending it below the chart - the panel wasn't sized to fit that,
      // which is what was cutting off the bottom of the chart.
      legend: {
        live: false,
        mount: (_u, el) => {
          this.legendEl.innerHTML = "";
          this.legendEl.appendChild(el);
        },
      },
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 0) {
              const min = u.posToVal(u.select.left, "x");
              const max = u.posToVal(u.select.left + u.select.width, "x");
              u.setScale("x", { min, max });
              u.setSelect({ left: 0, width: 0, top: 0, height: 0 }, false);
            }
          },
        ],
        setCursor: [
          (u) => {
            const idx = u.cursor.idx;
            specs.forEach((spec, i) => {
              const seriesIdx = i + 1;
              const el = tipEls[i];
              const val = idx == null ? null : (u.data[seriesIdx][idx] as number | null);
              if (val == null || !u.series[seriesIdx].show) {
                el.style.display = "none";
                return;
              }
              el.style.display = "block";
              el.style.left = `${u.valToPos(u.data[0][idx!] as number, "x")}px`;
              el.style.top = `${u.valToPos(val, spec.scale)}px`;
              el.textContent = `${val.toFixed(spec.scale === "depth" ? 2 : 1)} ${spec.unit}`;
            });
          },
        ],
      },
      data,
    };

    this.plot = new uPlot(opts, data, this.bodyEl);
    this.attachWheelZoomAndPan(this.plot);
    tipEls.forEach((el) => this.plot!.over.appendChild(el));

    // The legend (mounted into the header via `legend.mount` above) can wrap
    // onto multiple lines once it's actually populated with this site's
    // series, growing the header - which shrinks the body area below what
    // `chartSize()` measured before the plot existed. One corrective resize
    // now accounts for that; window resizes are handled separately below.
    this.resize();
  }

  // Wheel = zoom in/out centered on the cursor; shift+wheel = pan without
  // changing the zoom level (for a plain mouse wheel, which only ever
  // reports vertical delta); a trackpad's native two-finger left/right swipe
  // pans directly, no shift needed, since that gesture reports a horizontal
  // delta on its own. Double-click = reset to the full loaded range. uPlot
  // has no built-in wheel-zoom - this follows the standard recipe from
  // uPlot's own demos, adapted to also support panning.
  private attachWheelZoomAndPan(u: uPlot): void {
    u.over.addEventListener(
      "wheel",
      (e: WheelEvent) => {
        e.preventDefault();
        const { left } = u.cursor;
        if (left === undefined || left === null) return;
        const xMin = u.scales.x.min ?? this.fullRange!.min;
        const xMax = u.scales.x.max ?? this.fullRange!.max;
        const range = xMax - xMin;

        const isTrackpadSwipe = Math.abs(e.deltaX) > Math.abs(e.deltaY);
        if (e.shiftKey || isTrackpadSwipe) {
          const delta = isTrackpadSwipe ? e.deltaX : e.deltaY;
          const panBy = (delta / u.bbox.width) * range;
          u.setScale("x", { min: xMin + panBy, max: xMax + panBy });
        } else {
          const leftPct = left / u.bbox.width;
          const xVal = u.posToVal(left, "x");
          const factor = e.deltaY < 0 ? 0.85 : 1 / 0.85;
          const newRange = range * factor;
          const newMin = xVal - leftPct * newRange;
          u.setScale("x", { min: newMin, max: newMin + newRange });
        }
      },
      { passive: false },
    );
    u.over.addEventListener("dblclick", () => this.resetZoom());
  }

  private createTipEl(color: string): HTMLElement {
    const el = document.createElement("div");
    el.className = "chart-point-tip";
    el.style.display = "none";
    el.style.borderColor = color;
    el.style.color = color;
    return el;
  }

  private resetZoom(): void {
    if (this.plot && this.fullRange) {
      this.plot.setScale("x", { min: this.fullRange.min, max: this.fullRange.max });
    }
  }

  // Renders the currently-visible series into a fixed 16:9 PNG (logo upper
  // right, a legend, no title - presentation-ready, not a screenshot of the
  // live panel) and hands it to the browser's save dialog. The zoom range
  // stays whatever's currently selected (double-click still resets it, same
  // as before the Reset zoom button was replaced by this one).
  private async exportImage(): Promise<void> {
    if (!this.plot || this.currentSpecs.length === 0) return;

    const button = document.querySelector<HTMLButtonElement>("#chart-export")!;
    button.disabled = true;
    try {
      // Choosing where to save has to happen FIRST, before any rendering -
      // showSaveFilePicker() only works while still "handling a user
      // gesture," a short-lived window Chrome starts counting down from the
      // instant this click fired. The first version of this feature did the
      // (multi-step, async) chart re-render before asking where to save,
      // which regularly ran out that window and made the native picker fail
      // with "Must be handling a user gesture to show a file picker" -
      // reported after it shipped, and only sometimes on a second click
      // once. It also means there's no separate "type a filename" prompt
      // anymore on top of the native dialog: showSaveFilePicker's own
      // suggestedName field is already an editable filename box in the same
      // dialog, so a second, redundant text prompt in front of it was both
      // the cause of the bug and pointless UI once removed.
      const target = await chooseSaveTarget(`${this.suggestedExportBaseName()}.png`);
      if (target === null) return; // user cancelled

      const logo = await this.loadLogo();
      const blob = await this.renderExportImage(logo);
      await target.save(blob);
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      button.disabled = false;
    }
  }

  private loadLogo(): Promise<HTMLImageElement> {
    if (!this.logoImagePromise) {
      this.logoImagePromise = new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("failed to load the Mid-Columbia logo for the export"));
        img.src = "/logo.png";
      });
    }
    return this.logoImagePromise;
  }

  private async renderExportImage(logo: HTMLImageElement): Promise<Blob> {
    const u = this.plot!;
    // Current on/off state per series lives on the live uPlot instance
    // (mutated by the toggle checkboxes via setSeries), not on the original
    // SeriesSpec.show defaults - reflect exactly what's currently visible,
    // both in the legend and in the freshly re-rendered chart below.
    // renderedSpecs (not currentSpecs, the raw pre-year-expansion base list)
    // is what's 1:1 aligned with u.series[1..] - year mode can plot more
    // series than there are base specs.
    const exportSpecs = this.renderedSpecs.map((spec, i) => ({ ...spec, show: u.series[i + 1]?.show ?? spec.show }));
    const visibleSpecs = exportSpecs.filter((s) => s.show);
    const xRange = { min: u.scales.x!.min ?? this.fullRange!.min, max: u.scales.x!.max ?? this.fullRange!.max };

    const canvas = document.createElement("canvas");
    canvas.width = EXPORT_WIDTH * EXPORT_SCALE;
    canvas.height = EXPORT_HEIGHT * EXPORT_SCALE;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(EXPORT_SCALE, EXPORT_SCALE);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, EXPORT_WIDTH, EXPORT_HEIGHT);

    const logoHeight = EXPORT_LOGO_HEIGHT;
    const logoWidth = (logo.width / logo.height) * logoHeight;
    ctx.drawImage(logo, EXPORT_WIDTH - EXPORT_PAD - logoWidth, EXPORT_PAD * 0.5, logoWidth, logoHeight);

    const legendRows = layoutLegend(ctx, visibleSpecs, EXPORT_WIDTH - EXPORT_PAD * 2);
    const legendHeight = legendRows.length > 0 ? legendRows.length * EXPORT_LEGEND_ROW_HEIGHT + EXPORT_PAD : 0;

    const chartX = EXPORT_PAD;
    const chartY = EXPORT_PAD * 0.5 + logoHeight + EXPORT_PAD * 0.5;
    const chartWidth = Math.round(EXPORT_WIDTH - EXPORT_PAD * 2);
    const chartHeight = Math.round(EXPORT_HEIGHT - chartY - legendHeight - EXPORT_PAD);

    // A genuinely fresh, off-screen uPlot instance built at exactly the
    // export's target size, rather than resizing-then-restoring the live
    // on-screen one - resizing an already-rendered instance turned out to
    // visibly distort the result (everything stretched vertically, reported
    // after the first version of this feature shipped), so this renders
    // once at the real target dimensions instead of trying to reshape an
    // existing render after the fact.
    const chartBitmap = await this.renderChartBitmap(exportSpecs, chartWidth, chartHeight, xRange);
    try {
      ctx.drawImage(chartBitmap, chartX, chartY, chartWidth, chartHeight);
    } finally {
      chartBitmap.close();
    }

    drawLegend(ctx, legendRows, EXPORT_PAD, EXPORT_HEIGHT - legendHeight);

    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error("failed to render the chart export image"));
      }, "image/png");
    });
  }

  // Builds a one-off uPlot instance in a detached, off-screen container at
  // exactly (width, height), captures it as a bitmap, then tears it down -
  // isolated from the live/on-screen chart entirely, so the export can never
  // visibly disturb it and doesn't inherit any of its current DOM/CSS state.
  private async renderChartBitmap(
    specs: SeriesSpec[],
    width: number,
    height: number,
    xRange: { min: number; max: number },
  ): Promise<ImageBitmap> {
    const { series, scales, axes, data } = this.buildChartLayout(specs);
    scales.x = { ...scales.x, range: [xRange.min, xRange.max] };

    const container = document.createElement("div");
    container.style.position = "fixed";
    container.style.left = "-10000px";
    container.style.top = "0";
    document.body.appendChild(container);

    try {
      // uPlot defers its actual first paint to a microtask (its internal
      // commit() -> queueMicrotask(_commit)) rather than drawing
      // synchronously inside the constructor - capturing the canvas right
      // after `new uPlot(...)` grabbed it still blank. `ready` fires once
      // that deferred draw has genuinely happened, so wait for it instead
      // of assuming construction == painted (the bug behind the first
      // version of this rewrite: a blank chart under the export logo).
      return await new Promise<ImageBitmap>((resolve, reject) => {
        new uPlot(
          {
            width,
            height,
            series,
            scales,
            axes,
            legend: { show: false },
            cursor: { show: false },
            hooks: {
              ready: [
                (u) => {
                  createImageBitmap(u.ctx.canvas)
                    .then(resolve, reject)
                    .finally(() => u.destroy());
                },
              ],
            },
          },
          data,
          container,
        );
      });
    } finally {
      container.remove();
    }
  }

  private suggestedExportBaseName(): string {
    const base = (this.titleEl.textContent ?? "chart")
      .replace(/\s*›\s*/g, " - ")
      .replace(/[<>:"/\\|?*]/g, "-")
      .trim();
    return base || "chart";
  }

  private resize(): void {
    if (this.plot) {
      this.plot.setSize(this.chartSize());
    }
  }

  // `clientWidth`/`clientHeight` include #chart-panel-body's own padding,
  // but the uPlot instance is a child placed inside that padded content box -
  // sizing it to the full client dimensions (padding included) made it
  // overflow the panel by exactly the padding amount, pushing content below
  // the fold (the bug the user reported).
  private chartSize(): { width: number; height: number } {
    const style = getComputedStyle(this.bodyEl);
    const paddingX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const paddingY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    return {
      width: this.bodyEl.clientWidth - paddingX,
      height: this.bodyEl.clientHeight - paddingY,
    };
  }
}

interface LegendItem {
  label: string;
  color: string;
  dash?: number[];
}

// Wraps visible series into rows that fit `maxWidth`, measured up front so
// exportImage() knows how tall to make the legend band before it lays out
// the chart above it. A hand-drawn canvas legend (swatch + label per series)
// rather than rasterizing the on-screen uPlot legend DOM/CSS - simpler and
// more reliable across browsers than serializing arbitrary HTML into an
// image, and this app has no other DOM-to-image dependency to reach for.
function layoutLegend(ctx: CanvasRenderingContext2D, specs: SeriesSpec[], maxWidth: number): LegendItem[][] {
  ctx.font = EXPORT_FONT;
  const rows: LegendItem[][] = [];
  let row: LegendItem[] = [];
  let rowWidth = 0;
  for (const spec of specs) {
    const itemWidth = EXPORT_LEGEND_SWATCH_WIDTH + 6 + ctx.measureText(spec.label).width + EXPORT_LEGEND_ITEM_GAP;
    if (row.length > 0 && rowWidth + itemWidth > maxWidth) {
      rows.push(row);
      row = [];
      rowWidth = 0;
    }
    row.push({ label: spec.label, color: spec.color, dash: spec.dash });
    rowWidth += itemWidth;
  }
  if (row.length > 0) rows.push(row);
  return rows;
}

function drawLegend(ctx: CanvasRenderingContext2D, rows: LegendItem[][], x: number, y: number): void {
  ctx.font = EXPORT_FONT;
  ctx.textBaseline = "middle";
  rows.forEach((row, rowIndex) => {
    let cursorX = x;
    const rowY = y + rowIndex * EXPORT_LEGEND_ROW_HEIGHT + EXPORT_LEGEND_ROW_HEIGHT / 2;
    for (const item of row) {
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 2.5;
      ctx.setLineDash(item.dash ?? []);
      ctx.beginPath();
      ctx.moveTo(cursorX, rowY);
      ctx.lineTo(cursorX + EXPORT_LEGEND_SWATCH_WIDTH - 6, rowY);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = EXPORT_TEXT_COLOR;
      ctx.fillText(item.label, cursorX + EXPORT_LEGEND_SWATCH_WIDTH, rowY);

      cursorX += EXPORT_LEGEND_SWATCH_WIDTH + 6 + ctx.measureText(item.label).width + EXPORT_LEGEND_ITEM_GAP;
    }
  });
}

interface SaveTarget {
  save(blob: Blob): Promise<void>;
}

// Resolves *where* to save before anything gets rendered (see exportImage's
// comment on why the ordering matters). Prefers the native "choose where to
// save" dialog (File System Access API) - its own suggestedName field is
// already an editable filename box, so that single native dialog covers
// both "pick a location" and "let the user name the file." Falls back to a
// plain <a download> click on browsers without it (Firefox, Safari as of
// writing), which has no naming UI of its own - a lightweight prompt() only
// runs there, once, to make up for it.
async function chooseSaveTarget(suggestedName: string): Promise<SaveTarget | null> {
  if (window.showSaveFilePicker) {
    let handle: FileSystemFileHandle;
    try {
      handle = await window.showSaveFilePicker({
        suggestedName,
        types: [{ description: "PNG image", accept: { "image/png": [".png"] } }],
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return null; // user cancelled - not an error
      throw err;
    }
    return {
      async save(blob: Blob) {
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      },
    };
  }

  const name = prompt("File name for the exported chart image:", suggestedName.replace(/\.png$/i, ""));
  if (name === null) return null; // user cancelled
  const filename = `${name.replace(/\.png$/i, "").trim() || "chart"}.png`;
  return {
    async save(blob: Blob) {
      const url = URL.createObjectURL(blob);
      try {
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    },
  };
}

function toPointMap(points: { timestamp_utc: string; value: number | null }[]): Map<number, number> {
  const buckets = new Map<number, { sum: number; n: number }>();
  for (const p of points) {
    if (p.value === null) continue;
    const hour = Math.round(new Date(p.timestamp_utc).getTime() / 1000 / HOUR_SECONDS) * HOUR_SECONDS;
    const bucket = buckets.get(hour);
    if (bucket) {
      bucket.sum += p.value;
      bucket.n++;
    } else {
      buckets.set(hour, { sum: p.value, n: 1 });
    }
  }
  const result = new Map<number, number>();
  for (const [hour, { sum, n }] of buckets) result.set(hour, sum / n);
  return result;
}

// A jump this large between two consecutive real samples counts as a
// genuine gap (e.g. a logger pulled for the off-season) rather than a
// normal missed-reading blip - see the comment below on why it needs a
// synthetic grid point.
const GAP_THRESHOLD_SECONDS = HOUR_SECONDS * 24;

function buildHourGrid(specs: SeriesSpec[]): number[] {
  const keys = new Set<number>();
  for (const spec of specs) {
    for (const hour of spec.points.keys()) keys.add(hour);
  }
  const sorted = [...keys].sort((a, b) => a - b);

  // The grid is only the union of hours where *some* series actually has a
  // reading - fine for density (no reason to carry thousands of all-null
  // hourly slots through an active deployment), but it means a real gap
  // between two clusters (e.g. no well at this site logged anything for
  // months) has no grid point in between at all. With no index to be null
  // at, series.spanGaps: false (chart.ts's render()) has nothing to break
  // on, so uPlot just connects the last point before the gap straight to
  // the first point after it - fabricating a trend across months with no
  // actual readings. This became reachable for the first time once
  // open()/openAtm() started fetching full multi-year history instead of
  // just the current year (year-over-year comparison work) - caught by
  // hovering mid-"gap" in a live browser and seeing a real tooltip value
  // where there should have been nothing to hover. One synthetic timestamp
  // inserted mid-gap - deliberately absent from every series' own points,
  // so `spec.points.get(t) ?? null` is null for all of them - gives
  // spanGaps something to actually break at.
  const grid: number[] = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i - 1] > GAP_THRESHOLD_SECONDS) {
      grid.push(Math.floor((sorted[i - 1] + sorted[i]) / 2));
    }
    grid.push(sorted[i]);
  }
  return grid;
}

// Union of every calendar year (UTC) present across every spec's points,
// regardless of that spec's current `show` state - a year toggle should
// still be offered for e.g. water temperature even while that checkbox is
// off, since checking it later shouldn't require re-detecting years.
function computeAvailableYears(specs: SeriesSpec[]): number[] {
  const years = new Set<number>();
  for (const spec of specs) {
    for (const epochSeconds of spec.points.keys()) {
      years.add(new Date(epochSeconds * 1000).getUTCFullYear());
    }
  }
  return [...years].sort((a, b) => a - b);
}

// Filters `points` down to one real calendar year, then rewrites each
// timestamp's year to YEAR_REFERENCE (keeping month/day/hour) so different
// years' data can share one Jan-Dec x-axis.
function remapPointsToYear(points: Map<number, number>, year: number): Map<number, number> {
  const result = new Map<number, number>();
  for (const [epochSeconds, value] of points) {
    const d = new Date(epochSeconds * 1000);
    if (d.getUTCFullYear() !== year) continue;
    const remapped = Date.UTC(YEAR_REFERENCE, d.getUTCMonth(), d.getUTCDate(), d.getUTCHours()) / 1000;
    result.set(remapped, value);
  }
  return result;
}

// Custom x-axis tick formatter for year mode - "Mon D", deliberately never
// printing YEAR_REFERENCE itself, since that year is synthetic and would
// otherwise misleadingly imply the data is literally from the year 2000.
function formatMonthOnlyAxisValues(_u: uPlot, splits: number[]): (string | null)[] {
  return splits.map((s) => {
    const d = new Date(s * 1000);
    return `${MONTH_ABBR[d.getUTCMonth()]} ${d.getUTCDate()}`;
  });
}
