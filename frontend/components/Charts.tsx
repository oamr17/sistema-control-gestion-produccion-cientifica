"use client";

import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  ChartOptions,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Plugin,
  Tooltip
} from "chart.js";
import { Bar } from "react-chartjs-2";

import type { CareerKpi } from "@/lib/types";

const valueLabelPlugin: Plugin = {
  id: "value-labels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const isLineChart = (chart.config as { type?: string }).type === "line";
    ctx.save();
    ctx.fillStyle = "#374151";
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chart.getDatasetMeta(datasetIndex);
      if (meta.hidden) return;
      meta.data.forEach((element, index) => {
        const rawValue = dataset.data[index];
        const value = typeof rawValue === "number" ? rawValue : Number(rawValue);
        if (!Number.isFinite(value) || value === 0) return;
        const position = element.tooltipPosition(true);
        ctx.fillText(isLineChart ? `${value}%` : String(value), position.x, position.y - 6);
      });
    });
    ctx.restore();
  }
};

ChartJS.register(CategoryScale, LinearScale, BarElement, LineElement, PointElement, Tooltip, Legend, valueLabelPlugin);

const barScales: ChartOptions<"bar">["scales"] = {
  y: {
    beginAtZero: true,
    grace: "22%",
    grid: { color: "#D8EAF7", drawTicks: false },
    ticks: { display: false },
    border: { display: false }
  },
  x: {
    grid: { display: false },
    border: { display: false }
  }
};

const lineScales: ChartOptions<"line">["scales"] = {
  y: {
    beginAtZero: true,
    grace: "24%",
    grid: { color: "#D8EAF7", drawTicks: false },
    ticks: { display: false },
    border: { display: false }
  },
  x: {
    grid: { display: false },
    border: { display: false }
  }
};

const barPlugins = {
  datalabels: {
    anchor: "end",
    align: "top",
    color: "#374151",
    font: {
      size: 11,
      weight: "bold"
    },
    clamp: true,
    formatter: (value: number) => (value === 0 ? "" : value)
  },
  legend: {
    display: false
  }
} as unknown as ChartOptions<"bar">["plugins"];

const linePlugins = {
  legend: {
    display: false
  },
  datalabels: {
    align: "top",
    anchor: "end",
    color: "#374151",
    font: {
      size: 11,
      weight: "bold"
    },
    clamp: true,
    offset: 6,
    formatter: (value: number) => (value === 0 ? "" : `${value}%`)
  }
} as unknown as ChartOptions<"line">["plugins"];

const options: ChartOptions<"bar"> = {
  responsive: true,
  maintainAspectRatio: false,
  layout: {
    padding: {
      top: 28,
      right: 18,
      bottom: 4,
      left: 18
    }
  },
  plugins: barPlugins,
  scales: barScales
};

const lineOptions: ChartOptions<"line"> = {
  responsive: true,
  maintainAspectRatio: false,
  layout: {
    padding: {
      top: 34,
      right: 22,
      bottom: 4,
      left: 22
    }
  },
  plugins: linePlugins,
  scales: lineScales
};

function ChartLegend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex min-h-6 flex-wrap items-center justify-center gap-x-5 gap-y-2 text-xs text-ink/65">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-2">
          <span className="h-3 w-3 rounded-[2px]" style={{ backgroundColor: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

export function CareerComparisonChart({ careers }: { careers: CareerKpi[] }) {
  return (
    <div className="mt-4">
      <ChartLegend items={[{ label: "Producción científica", color: "#2F855A" }]} />
      <div className="mt-3 h-72 overflow-hidden">
        <Bar
          options={options}
          data={{
            labels: careers.map((item) => item.career_name),
            datasets: [
              {
                label: "Producción científica",
                data: careers.map((item) => item.scientific_output_total),
                backgroundColor: "#2F855A"
              }
            ]
          }}
        />
      </div>
    </div>
  );
}
