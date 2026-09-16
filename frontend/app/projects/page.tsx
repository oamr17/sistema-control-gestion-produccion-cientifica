"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { api } from "@/lib/api";
import { subscribeEffectiveDataRefresh } from "@/lib/effective-data-refresh";
import { useGlobalFilters } from "@/lib/filters";
import type { Project } from "@/lib/types";

const projectTypeLabel: Record<string, string> = {
  FCI: "Proyecto FCI",
  SEEDBED: "Semillero",
  proyecto_fci: "Proyecto FCI",
  grupo_investigacion: "Grupo de investigación",
  semillero: "Semillero",
  informe_seguimiento: "Informe de seguimiento",
  pendiente_clasificacion: "Pendiente de clasificación"
};

function LegacyProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const { periods, yearLabel, cycle, effectiveCareerId } = useGlobalFilters();
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const periodId = periods.find((period) => period.year_label === yearLabel && period.cycle === cycle)?.id;

  const reloadProjects = useCallback(async () => {
    const params = new URLSearchParams();
    if (periodId) params.set("period_id", String(periodId));
    if (effectiveCareerId) params.set("career_id", effectiveCareerId);
    const query = params.toString() ? `?${params}` : "";
    const result = await api.projects(query);
    setProjects(status ? result.filter((project) => project.status === status) : result);
  }, [effectiveCareerId, periodId, status]);

  useEffect(() => {
    void reloadProjects();
  }, [reloadProjects]);

  useEffect(() => subscribeEffectiveDataRefresh(() => {
    void reloadProjects();
  }), [reloadProjects]);

  return (
    <AppShell>
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Investigación aplicada</p>
          <h2 className="mt-2 text-3xl font-semibold">Proyectos</h2>
        </div>
        <button className="focus-ring rounded-[8px] bg-ink px-4 py-3 text-sm font-semibold text-white">
          Registrar proyecto
        </button>
      </header>
      <section className="mt-6">
        <DataTable
          rows={projects}
          columns={[
            {
              key: "name",
              label: "Proyecto"
            },
            { key: "project_type", label: "Tipo", render: (item) => projectTypeLabel[item.project_type] ?? item.project_type },
            { key: "year_label", label: "Año" },
            { key: "cycle", label: "Ciclo" },
            {
              key: "teachers",
              label: "Participantes",
              render: (item) => item.teachers.map((teacher) => teacher.teacher_name).join(", ") || "Sin participantes"
            },
            { key: "status", label: "Estado" },
            { key: "progress_percentage", label: "Avance", render: (item) => `${item.progress_percentage}%` }
          ]}
          renderExpanded={(project) => (
            <div className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
              <div>
                <h3 className="text-sm font-semibold text-ink">Información del proyecto</h3>
                <div className="mt-3 space-y-2 text-sm text-ink/70">
                  <p>Tipo: {projectTypeLabel[project.project_type] ?? project.project_type}</p>
                  <p>Periodo: {project.year_label}, ciclo {project.cycle}</p>
                  <p>Avance: {project.progress_percentage}%</p>
                  <p>Estado: {project.status}</p>
                  <p>Descripción: {project.description ?? "Sin descripción"}</p>
                </div>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-ink">Participantes</h3>
                {project.teachers.length ? (
                  <div className="mt-3 space-y-3 text-sm text-ink/70">
                    {project.teachers.map((teacher) => (
                      <div key={teacher.id} className="rounded-[8px] border border-line bg-white p-3">
                        <p className="font-semibold text-ink">{teacher.teacher_name}</p>
                        <p>{teacher.role === "DIRECTOR" ? "Director" : "Investigador"}</p>
                        <p>{teacher.career_name} · {teacher.faculty_name}</p>
                        <p>{teacher.teacher_email}</p>
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-3 text-sm text-ink/70">Sin participantes registrados</p>}
              </div>
            </div>
          )}
        />
      </section>
    </AppShell>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense fallback={null}>
      <LegacyProjectsPage />
    </Suspense>
  );
}
