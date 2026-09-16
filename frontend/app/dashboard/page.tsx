"use client";

import Link from "next/link";
import {
  BookMarked,
  CheckCircle2,
  FileText,
  FolderKanban,
  Percent,
  ScanText,
  University,
  Users
} from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { CareerComparisonChart } from "@/components/Charts";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { useCachedQuery } from "@/lib/data-cache";
import { useGlobalFilters } from "@/lib/filters";
import type { DashboardKpi, ImportJob, ImportStatus, NamedCount } from "@/lib/types";

function safeNumber(value: number | null | undefined) {
  return value ?? 0;
}

function safeItems(items: NamedCount[] | undefined) {
  return items?.length ? items : [];
}

function careerHref(basePath: string, item: NamedCount) {
  return item.id ? `${basePath}?career_id=${item.id}` : basePath;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "Sin fecha";
  return new Intl.DateTimeFormat("es-EC", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function statusLabel(status: string | null | undefined) {
  const normalized = status?.toLowerCase() ?? "";
  if (normalized.includes("review") || normalized.includes("revision")) return "Requiere revision";
  if (normalized.includes("processed") || normalized.includes("proces")) return "Procesado";
  if (normalized.includes("failed") || normalized.includes("error")) return "Error";
  if (normalized.includes("ignored") || normalized.includes("ignorado")) return "Ignorado";
  if (normalized.includes("processing") || normalized.includes("procesando")) return "Procesando";
  if (normalized.includes("queued") || normalized.includes("cola")) return "En cola";
  return status || "Sin estado";
}

function statusClasses(status: string | null | undefined) {
  const normalized = status?.toLowerCase() ?? "";
  if (normalized.includes("review") || normalized.includes("revision")) return "bg-amber/25 text-[#705500]";
  if (normalized.includes("processed") || normalized.includes("proces")) return "bg-mint/10 text-mint";
  if (normalized.includes("failed") || normalized.includes("error")) return "bg-coral/10 text-coral";
  if (normalized.includes("ignored") || normalized.includes("ignorado")) return "bg-grape/30 text-ink";
  return "bg-paper text-ink/65";
}

function SectionCard({
  title,
  href,
  children
}: {
  title: string;
  href?: string;
  children: React.ReactNode;
}) {
  return (
    <article className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
      <div className="flex items-start justify-between gap-4">
        <h3 className="text-lg font-semibold text-ink">{title}</h3>
        {href ? (
          <Link
            href={href}
            className="rounded-[8px] bg-mint/10 px-3 py-2 text-xs font-semibold text-mint transition hover:bg-mint/15"
          >
            Ver detalle
          </Link>
        ) : null}
      </div>
      {children}
    </article>
  );
}

function EmptyState() {
  return (
    <div className="mt-4 flex min-h-64 items-center justify-center rounded-[8px] border border-dashed border-line bg-paper/50 px-4 text-center text-sm text-ink/55">
      Sin datos disponibles para este filtro
    </div>
  );
}

function RankedBars({
  items,
  accent,
  hrefFor
}: {
  items: NamedCount[];
  accent: "mint" | "grape";
  hrefFor: (item: NamedCount) => string;
}) {
  const max = Math.max(...items.map((item) => item.count), 0);
  const color = accent === "mint" ? "bg-mint" : "bg-[#7C6BC8]";

  if (!items.length) return <EmptyState />;

  return (
    <div className="mt-5 space-y-4">
      {items.slice(0, 6).map((item) => {
        const width = max > 0 ? Math.max((item.count / max) * 100, 8) : 0;
        return (
          <Link key={`${item.id ?? item.name}-${item.count}`} href={hrefFor(item)} className="block rounded-[8px] p-2 transition hover:bg-paper/70">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="line-clamp-1 font-medium text-ink/75">{item.name || "Sin datos"}</span>
              <strong className="text-ink">{safeNumber(item.count)}</strong>
            </div>
            <div className="mt-2 h-3 rounded-full bg-paper">
              <div className={`h-3 rounded-full ${color}`} style={{ width: `${width}%` }} />
            </div>
          </Link>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const { yearLabel, cycle, effectiveCareerId, role } = useGlobalFilters();
  const dashboardQuery = useCachedQuery<DashboardKpi>(
    `dashboard:${yearLabel}:${cycle}:${effectiveCareerId || "all"}`,
    () => api.dashboardSummary(yearLabel, cycle, effectiveCareerId),
    { enabled: Boolean(role) }
  );
  const importJobsQuery = useCachedQuery<ImportJob[]>(
    "imports-jobs:dashboard-latest",
    () => api.importJobs(),
    { enabled: role === "FACULTY_ADMIN", staleTimeMs: 30_000 }
  );
  const importStatusQuery = useCachedQuery<ImportStatus>(
    "imports-latest-status",
    () => api.latestImportStatus(),
    { enabled: role === "FACULTY_ADMIN", staleTimeMs: 15_000 }
  );

  const dashboard = dashboardQuery.data;
  const careers = dashboard?.careers ?? [];
  const productionByCareer = safeItems(dashboard?.production_by_career);
  const teachersByCareer = safeItems(dashboard?.internal_teachers_by_career);
  const externalResearchers = safeItems(dashboard?.external_researchers_by_university);
  const recentJobs = (importJobsQuery.data ?? []).slice(0, 5);
  const importStatus = importStatusQuery.data;
  const finishedImports =
    (importStatus?.processed ?? 0) +
    (importStatus?.ignored ?? 0) +
    (importStatus?.requires_review ?? 0) +
    (importStatus?.failed ?? 0);

  return (
    <AppShell>
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Panel de investigacion</p>
          <h2 className="mt-2 text-3xl font-semibold text-ink">Dashboard de produccion cientifica</h2>
          <p className="mt-2 max-w-3xl text-sm text-ink/60">
            Resumen general de informes, docentes, produccion cientifica y proyectos del periodo seleccionado.
          </p>
        </div>
        <p className="rounded-[8px] border border-line bg-white px-4 py-3 text-sm text-ink/60 shadow-soft">
          Año {yearLabel} · Ciclo {cycle}
        </p>
      </header>

      {dashboardQuery.isUpdating ? (
        <p className="mt-4 rounded-full bg-mint/10 px-3 py-1 text-xs font-semibold text-mint">
          Actualizando datos...
        </p>
      ) : null}

      {importStatus?.active ? (
        <Link
          href="/importaciones"
          className="mt-4 inline-flex rounded-full border border-line bg-white px-4 py-2 text-sm font-medium text-ink/70 shadow-soft transition hover:border-mint/50"
        >
          Procesando {finishedImports}/{importStatus.total} informes
        </Link>
      ) : null}

      {dashboardQuery.isInitialLoading ? (
        <p className="mt-6 rounded-[8px] border border-line bg-white p-4 text-sm text-ink/60 shadow-soft">
          Cargando indicadores del periodo seleccionado...
        </p>
      ) : null}

      <section className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <StatCard
          label="Informes recibidos"
          value={String(safeNumber(dashboard?.reports_received))}
          helper="Registros persistidos"
          icon={FileText}
          accent="mint"
          href="/importaciones"
        />
        <StatCard
          label="Informes que requieren revision"
          value={String(safeNumber(dashboard?.reports_requires_review))}
          helper="Pendientes de validacion"
          icon={ScanText}
          accent="coral"
          href="/importaciones?status=requires_review"
        />
        <StatCard
          label="Docentes activos"
          value={String(safeNumber(dashboard?.total_teachers))}
          helper={`${safeNumber(dashboard?.internal_fca_count)} FCA / ${safeNumber(dashboard?.internal_other_faculty_count)} otra facultad`}
          icon={Users}
          accent="mint"
          href="/teachers"
        />
        <StatCard
          label="Productos elegibles KPI"
          value={String(safeNumber(dashboard?.scientific_output_kpi_eligible ?? dashboard?.scientific_output_total))}
          helper={`${safeNumber(dashboard?.scientific_output_detected)} detectados · ${safeNumber(dashboard?.scientific_output_pending_review)} pendientes · ${safeNumber(dashboard?.scientific_output_discarded)} descartados`}
          icon={BookMarked}
          accent="grape"
          href="/production"
        />
        <StatCard
          label="Entidades elegibles KPI"
          value={String(safeNumber(dashboard?.projects_total))}
          helper={`${safeNumber(dashboard?.research_entities_detected)} detectadas · ${safeNumber(dashboard?.research_entities_pending_review)} pendientes · ${safeNumber(dashboard?.research_entities_excluded)} excluidas`}
          icon={FolderKanban}
          accent="amber"
          href="/projects"
        />
        <StatCard
          label="Avance promedio"
          value={`${safeNumber(dashboard?.projects_average_progress_percent)}%`}
          helper={`${safeNumber(dashboard?.projects_approved)} aprobados`}
          icon={Percent}
          accent="coral"
          href="/projects"
        />
      </section>

      <section className="mt-4 grid gap-3 sm:grid-cols-3">
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos detectados</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{safeNumber(dashboard?.external_researchers_detected)}</p>
        </article>
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos validados KPI</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{safeNumber(dashboard?.external_researchers_kpi_eligible)}</p>
        </article>
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos pendientes</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{safeNumber(dashboard?.external_researchers_pending_review)}</p>
        </article>
      </section>

      <section className="mt-8 grid gap-5 xl:grid-cols-3">
        <SectionCard title="Participaciones por carrera" href="/production">
          {careers.length ? <CareerComparisonChart careers={careers} /> : <EmptyState />}
          <p className="mt-4 text-sm text-ink/60">
            Las barras cuentan participaciones; un producto puede atribuirse a varias carreras. Productos unicos KPI:{" "}
            <strong className="text-ink">{safeNumber(dashboard?.scientific_output_kpi_eligible ?? dashboard?.scientific_output_total)}</strong>
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <Link className="rounded-full bg-grape/30 px-3 py-1 font-semibold text-ink" href="/production">
              Detectados: {safeNumber(dashboard?.scientific_output_detected)}
            </Link>
            <Link className="rounded-full bg-mint/10 px-3 py-1 font-semibold text-mint" href="/production?status=published">
              Publicados: {safeNumber(dashboard?.scientific_output_published)}
            </Link>
            <Link className="rounded-full bg-coral/10 px-3 py-1 font-semibold text-coral" href="/production?status=en_revision">
              En revision: {safeNumber(dashboard?.scientific_output_in_review)}
            </Link>
            <Link className="rounded-full bg-amber/25 px-3 py-1 font-semibold text-[#705500]" href="/production?visibility=pending">
              Pendientes: {safeNumber(dashboard?.scientific_output_pending_review)}
            </Link>
            <Link className="rounded-full bg-paper px-3 py-1 font-semibold text-ink/65" href="/production?visibility=discarded">
              Descartados: {safeNumber(dashboard?.scientific_output_discarded)}
            </Link>
          </div>
        </SectionCard>

        <SectionCard title="Participaciones docentes por carrera" href="/teachers">
          <RankedBars items={teachersByCareer} accent="mint" hrefFor={(item) => careerHref("/teachers", item)} />
          <p className="mt-4 text-sm text-ink/60">
            Docentes con investigacion:{" "}
            <Link className="font-semibold text-mint" href="/teachers?research=true">
              {safeNumber(dashboard?.teachers_in_research_percent)}%
            </Link>
          </p>
        </SectionCard>

        <SectionCard title="Investigadores externos validados por universidad" href="/teachers">
          <RankedBars
            items={externalResearchers}
            accent="grape"
            hrefFor={(item) => `/teachers?external_university=${encodeURIComponent(item.name)}`}
          />
          <p className="mt-4 text-sm text-ink/60">
            <strong className="text-ink">{safeNumber(dashboard?.external_researchers_kpi_eligible)}</strong> validado de{" "}
            <strong className="text-ink">{safeNumber(dashboard?.external_researchers_detected)}</strong> detectados;{" "}
            <strong className="text-ink">{safeNumber(dashboard?.external_researchers_pending_review)}</strong> pendientes
          </p>
        </SectionCard>
      </section>

      <section className="mt-8 rounded-[8px] border border-line bg-white p-5 shadow-soft">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="text-mint" size={20} />
            <h3 className="text-lg font-semibold">Ultimos informes importados</h3>
          </div>
          <Link
            href="/importaciones"
            className="w-fit rounded-[8px] bg-mint/10 px-3 py-2 text-xs font-semibold text-mint transition hover:bg-mint/15"
          >
            Ver todos
          </Link>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-line text-sm">
            <thead className="bg-paper/80">
              <tr>
                <th className="px-4 py-3 text-left font-semibold text-ink/70">Archivo</th>
                <th className="px-4 py-3 text-left font-semibold text-ink/70">Estado</th>
                <th className="px-4 py-3 text-left font-semibold text-ink/70">Fecha de importacion</th>
                <th className="px-4 py-3 text-left font-semibold text-ink/70">Accion</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {recentJobs.length ? (
                recentJobs.map((job) => (
                  <tr key={job.id} className="hover:bg-paper/60">
                    <td className="px-4 py-3 font-medium text-ink/80">{job.filename || "Sin archivo"}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${statusClasses(job.status)}`}>
                        {statusLabel(job.status)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-ink/65">{formatDate(job.created_at)}</td>
                    <td className="px-4 py-3">
                      <Link className="rounded-[8px] border border-line px-3 py-2 text-xs font-semibold text-ink/70" href="/importaciones">
                        Ver detalle
                      </Link>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="px-4 py-6 text-center text-ink/55" colSpan={4}>
                    Sin datos
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}
