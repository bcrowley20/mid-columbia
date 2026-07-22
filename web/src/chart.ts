import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { fetchWellReadings } from "./api";
import type { ReachOut, SiteOut, WellOut } from "./types";

// Phase 6 detail view. Opens as a bottom panel over the map (not a separate
// browser window - see Implementation Plan.md section 6's design discussion)
// when a Site is clicked, plotting every well at that site plus the reach's
// ATM well as an optional overlay.

const HOUR_SECONDS = 3600;

// Base color per well type, with extra shades if a site has more than one
// well of the same type - matches the blue/red site/ATM marker convention
// already used on the map (map.ts), extended with a green family for IS.
const GW_SHADES = ["#2563eb", "#1d4ed8", "#3b82f6"];
const IS_SHADES = ["#059669", "#047857", "#10b981"];
const ATM_COLOR = "#dc2626";

interface SeriesSpec {
  label: string;
  color: string;
  scale: "depth" | "temp";
  dash?: number[];
  show: boolean;
  // Raw (timestamp epoch seconds -> value) points, before alignment to the
  // shared chart-wide time grid.
  points: Map<number, number>;
  unit: string;
}

export class ChartPanel {
  private readonly panel: HTMLElement;
  private readonly titleEl: HTMLElement;
  private readonly legendEl: HTMLElement;
  private readonly bodyEl: HTMLElement;
  private readonly waterTempCheckbox: HTMLInputElement;
  private readonly airTempCheckbox: HTMLInputElement;
  private plot: uPlot | null = null;
  private fullRange: { min: number; max: number } | null = null;
  private waterTempIndices: number[] = [];
  private airTempIndex: number | null = null;

  constructor() {
    this.panel = document.querySelector<HTMLElement>("#chart-panel")!;
    this.titleEl = document.querySelector<HTMLElement>("#chart-panel-title")!;
    this.legendEl = document.querySelector<HTMLElement>("#chart-panel-legend")!;
    this.bodyEl = document.querySelector<HTMLElement>("#chart-panel-body")!;
    this.waterTempCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-water-temp")!;
    this.airTempCheckbox = document.querySelector<HTMLInputElement>("#chart-toggle-air-temp")!;

    document.querySelector<HTMLButtonElement>("#chart-panel-close")!.addEventListener("click", () => this.close());
    document.querySelector<HTMLButtonElement>("#chart-reset-zoom")!.addEventListener("click", () => this.resetZoom());
    this.waterTempCheckbox.addEventListener("change", () => {
      for (const idx of this.waterTempIndices) this.plot?.setSeries(idx, { show: this.waterTempCheckbox.checked });
    });
    this.airTempCheckbox.addEventListener("change", () => {
      if (this.airTempIndex !== null) this.plot?.setSeries(this.airTempIndex, { show: this.airTempCheckbox.checked });
    });
    window.addEventListener("resize", () => this.resize());
  }

  close(): void {
    this.panel.hidden = true;
    this.plot?.destroy();
    this.plot = null;
  }

  async open(reach: ReachOut, site: SiteOut): Promise<void> {
    this.panel.hidden = false;
    this.titleEl.textContent = `${reach.name} › ${site.name}`;
    this.waterTempCheckbox.checked = false;
    this.airTempCheckbox.checked = false;
    this.bodyEl.innerHTML = `<div id="chart-panel-empty">Loading…</div>`;

    const yearStart = new Date(Date.UTC(new Date().getUTCFullYear(), 0, 1));

    const gwCount = { n: 0 };
    const isCount = { n: 0 };
    const wellSeries = await Promise.all(
      site.wells.map(async (well) => {
        const isGw = well.well_type === "groundwater";
        const shade = isGw ? GW_SHADES[gwCount.n % GW_SHADES.length] : IS_SHADES[isCount.n % IS_SHADES.length];
        if (isGw) gwCount.n++;
        else isCount.n++;
        return this.fetchWellSeries(well, shade, yearStart);
      }),
    );

    const atmSeries = await this.fetchAtmSeries(reach.atm_well, yearStart);

    const depthSeries = wellSeries.map((w) => w.depth).filter((s) => s.points.size > 0);
    const tempSeries = [...wellSeries.map((w) => w.temp), ...(atmSeries ? [atmSeries] : [])].filter(
      (s) => s.points.size > 0,
    );

    if (depthSeries.length === 0 && tempSeries.length === 0) {
      this.bodyEl.innerHTML = `<div id="chart-panel-empty">No readings for this site's wells yet.</div>`;
      return;
    }

    this.render([...depthSeries, ...tempSeries]);
  }

  private async fetchWellSeries(
    well: WellOut,
    color: string,
    from: Date,
  ): Promise<{ depth: SeriesSpec; temp: SeriesSpec }> {
    const [depthResult, tempResult] = await Promise.all([
      fetchWellReadings(well.id, "water_depth", from),
      fetchWellReadings(well.id, "water_temperature", from),
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
    };
  }

  private async fetchAtmSeries(atmWell: WellOut, from: Date): Promise<SeriesSpec | null> {
    const result = await fetchWellReadings(atmWell.id, "air_temperature", from);
    if (result.points.length === 0) return null;
    return {
      label: `${atmWell.name} air temp`,
      color: ATM_COLOR,
      scale: "temp",
      dash: [2, 3],
      show: false,
      unit: result.points[0]?.unit ?? "°F",
      points: toPointMap(result.points),
    };
  }

  private render(specs: SeriesSpec[]): void {
    // destroy() also detaches the legend even though it's mounted outside
    // `bodyEl` (into the header's #chart-panel-legend) - needed here since
    // open() can be called again, for a different site, without close() ever
    // running first (clicking straight from one site's marker to another's).
    this.plot?.destroy();
    this.plot = null;
    this.bodyEl.innerHTML = "";

    // uPlot requires every series to share one x-axis array. Loggers don't
    // necessarily sample on the exact same second, so timestamps are snapped
    // to the nearest hour to build one shared grid - the "interpolat[ion] for
    // display" the sponsor's brief called for. A null on that grid means this
    // series genuinely has no reading in that hour (rendered as a gap, not
    // bridged - spanGaps stays false), not a placeholder for another series.
    const grid = buildHourGrid(specs);
    const xs = grid.map((t) => t);
    const data: (number | null)[][] = [xs, ...specs.map((spec) => grid.map((t) => spec.points.get(t) ?? null))];

    this.waterTempIndices = [];
    this.airTempIndex = null;
    specs.forEach((spec, i) => {
      if (spec.scale === "temp") {
        if (spec.color === ATM_COLOR) this.airTempIndex = i + 1;
        else this.waterTempIndices.push(i + 1);
      }
    });

    const depthUnit = specs.find((s) => s.scale === "depth")?.unit ?? "ft";
    const tempUnit = specs.find((s) => s.scale === "temp")?.unit ?? "°F";

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

    const xMin = xs[0];
    const xMax = xs[xs.length - 1];
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
      scales: {
        x: { time: true },
        depth: {},
        temp: {},
      },
      axes: [
        {},
        { scale: "depth", label: `Depth (${depthUnit})`, side: 3 },
        { scale: "temp", label: `Temperature (${tempUnit})`, side: 1, grid: { show: false } },
      ],
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
      data: data as uPlot.AlignedData,
    };

    this.plot = new uPlot(opts, data as uPlot.AlignedData, this.bodyEl);
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

function buildHourGrid(specs: SeriesSpec[]): number[] {
  const keys = new Set<number>();
  for (const spec of specs) {
    for (const hour of spec.points.keys()) keys.add(hour);
  }
  return [...keys].sort((a, b) => a - b);
}
