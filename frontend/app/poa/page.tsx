"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { api } from "@/lib/api";
import { useGlobalFilters } from "@/lib/filters";
import type { GoalSummaryItem } from "@/lib/types";

export default function PoaPage() {
  const { yearLabel, cycle, effectiveCareerId } = useGlobalFilters();
  const [summary, setSummary] = useState<GoalSummaryItem[]>([]);

  useEffect(() => {
    api.goalSummary(yearLabel, cycle, effectiveCareerId).then(setSummary);
  }, [yearLabel, cycle, effectiveCareerId]);

  return (
    <AppShell>
      <header>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Planificación anual</p>
        <h2 className="mt-2 text-3xl font-semibold">POA</h2>
        <p className="mt-2 text-sm text-ink/60">
          Comparación entre metas planificadas y avance reportado para {yearLabel}, ciclo {cycle}.
        </p>
      </header>
      <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {summary.map((item) => (
          <article key={item.metric} className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
            <p className="text-sm text-ink/60">{item.label}</p>
            <strong className="mt-2 block text-3xl font-semibold">{item.compliance_percent}%</strong>
            <div className="mt-4 space-y-2 text-sm text-ink/65">
              <p>Planificado: {item.planned_value}</p>
              <p>Reportado: {item.reported_value}</p>
            </div>
          </article>
        ))}
      </section>
    </AppShell>
  );
}
