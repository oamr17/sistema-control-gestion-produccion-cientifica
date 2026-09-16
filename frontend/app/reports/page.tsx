"use client";

import { Download, FileText, Sheet } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { getToken } from "@/lib/api";
import { useGlobalFilters } from "@/lib/filters";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

async function downloadReport(kind: "pdf" | "excel", yearLabel: string, cycle: number) {
  const response = await fetch(`${API_URL}/reports/${kind}?year_label=${yearLabel}&cycle=${cycle}`, {
    headers: { Authorization: `Bearer ${getToken()}` }
  });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = kind === "pdf" ? "scientific-production.pdf" : "scientific-production.xlsx";
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function ReportsPage() {
  const { yearLabel, cycle } = useGlobalFilters();

  return (
    <AppShell>
      <header>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Reportería institucional</p>
        <h2 className="mt-2 text-3xl font-semibold">Reportes</h2>
        <p className="mt-2 text-sm text-ink/60">
          Exportación con filtros globales: {yearLabel}, ciclo {cycle}.
        </p>
      </header>
      <section className="mt-6 grid gap-4 md:grid-cols-3">
        {[
          { label: "Mensual", icon: FileText, kind: "pdf" as const },
          { label: "Por ciclo", icon: Sheet, kind: "excel" as const },
          { label: "Anual", icon: Download, kind: "pdf" as const }
        ].map((report) => {
          const Icon = report.icon;
          return (
            <article key={report.label} className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
              <Icon className="text-mint" size={24} />
              <h3 className="mt-4 text-xl font-semibold">{report.label}</h3>
              <p className="mt-2 text-sm text-ink/60">Exportación en PDF y Excel desde los datos consolidados.</p>
              <button
                onClick={() => downloadReport(report.kind, yearLabel, cycle)}
                className="focus-ring mt-5 rounded-[8px] border border-line px-4 py-2 text-sm font-semibold"
              >
                Generar
              </button>
            </article>
          );
        })}
      </section>
    </AppShell>
  );
}
