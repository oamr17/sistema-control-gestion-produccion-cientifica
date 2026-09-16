"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/auth";
import { useCachedQuery } from "@/lib/data-cache";
import { subscribeEffectiveDataRefresh } from "@/lib/effective-data-refresh";
import { useGlobalFilters } from "@/lib/filters";
import type { DashboardKpi, Production } from "@/lib/types";

const labelMap = {
  ARTICLE: "Artículo",
  BOOK: "Libro",
  BOOK_CHAPTER: "Capítulo",
  PRESENTATION: "Ponencia"
};

const validationLabel: Record<string, string> = {
  validated: "Validado",
  validado: "Validado",
  pending_review: "Pendiente de validación",
  pendiente_validacion: "Pendiente de validación",
  discarded_invalid: "Descartado"
};

function productValidationStatus(item: Production) {
  return item.validation_status ?? "validated";
}

function isPendingProduct(item: Production) {
  if (item.visibility === "pending") return true;
  const status = productValidationStatus(item);
  return status === "pending_review" || status === "pendiente_validacion" || item.status === "pending_review";
}

function isDiscardedProduct(item: Production) {
  return item.visibility === "discarded" || productValidationStatus(item) === "discarded_invalid";
}

function isTruncatedProduct(item: Production) {
  return /truncado|incompleto|fragment/i.test(item.review_reason ?? "");
}

function isKpiEligibleProduct(item: Production) {
  return item.kpi_eligible ?? (!isPendingProduct(item) && !isDiscardedProduct(item));
}

function safeMetric(value: number | null | undefined, fallback: number) {
  return value ?? fallback;
}

function productStatusLabel(item: Production) {
  if (isDiscardedProduct(item)) return "Descartado";
  if (isTruncatedProduct(item)) return "Pendiente por título truncado";
  if (isPendingProduct(item)) return "Pendiente";
  return "Elegible KPI";
}

function productBadgeClass(item: Production) {
  if (isDiscardedProduct(item)) return "border-rose-200 bg-rose-50 text-rose-700";
  if (isPendingProduct(item)) return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

function normalizeNameKey(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[.:]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toUpperCase();
}

function looksLikeRawProductTitle(value?: string | null) {
  const text = value ?? "";
  const key = normalizeNameKey(text);
  if (!key) return true;
  const tokens = key.split(" ").filter(Boolean);
  return (
    text.length > 120 ||
    tokens.length > 18 ||
    key.includes("HTTP") ||
    key.includes("WWW") ||
    key.includes("INDEX PHP") ||
    key.includes("ENVIADO A REVISION") ||
    key.includes("PUBLICA DO") ||
    key.includes("PUBLICADO REGIONAL") ||
    key.includes("ORTIZ GUEVARA") ||
    key.includes("MARIA DEL PILAR ORTEGA")
  );
}

function displayProductTitle(item: Production) {
  if (!item.title) return "Titulo pendiente de revision";
  if (isPendingProduct(item) && looksLikeRawProductTitle(item.title)) return "Titulo pendiente de revision";
  return item.title;
}

function rawProductText(item: Production) {
  const displayTitle = displayProductTitle(item);
  if (item.raw_title && item.raw_title !== displayTitle) return item.raw_title;
  if (displayTitle !== item.title) return item.raw_title || item.title;
  return null;
}

function LegacyProductionPage() {
  const searchParams = useSearchParams();
  const requestedVisibility = searchParams.get("visibility");
  const [production, setProduction] = useState<Production[]>([]);
  const [visibility, setVisibility] = useState<"all" | "eligible" | "pending" | "discarded">(
    requestedVisibility === "eligible" || requestedVisibility === "pending" || requestedVisibility === "discarded"
      ? requestedVisibility
      : "all"
  );
  const { periods, yearLabel, cycle, effectiveCareerId } = useGlobalFilters();
  const profile = getProfile();
  const periodId = periods.find((period) => period.year_label === yearLabel && period.cycle === cycle)?.id;
  const dashboardQuery = useCachedQuery<DashboardKpi>(
    `dashboard:${yearLabel}:${cycle}:${effectiveCareerId || "all"}`,
    () => api.dashboardSummary(yearLabel, cycle, effectiveCareerId),
    { enabled: Boolean(profile) }
  );
  const dashboardSummary = dashboardQuery.data;
  const detectedCount = safeMetric(dashboardSummary?.scientific_output_detected, production.length);
  const visibleCount = production.length;
  const eligibleCount = safeMetric(dashboardSummary?.scientific_output_kpi_eligible, production.filter(isKpiEligibleProduct).length);
  const pendingCount = safeMetric(dashboardSummary?.scientific_output_pending_review, production.filter(isPendingProduct).length);
  const discardedCount = safeMetric(dashboardSummary?.scientific_output_discarded, production.filter(isDiscardedProduct).length);

  const reloadProduction = useCallback(async () => {
    const params = new URLSearchParams();
    if (periodId) params.set("period_id", String(periodId));
    if (effectiveCareerId) params.set("career_id", effectiveCareerId);
    params.set("visibility", "all");
    const query = params.toString() ? `?${params}` : "";
    setProduction(await api.production(query));
  }, [effectiveCareerId, periodId]);

  useEffect(() => {
    void reloadProduction();
  }, [reloadProduction]);

  useEffect(() => subscribeEffectiveDataRefresh(() => {
    void reloadProduction();
  }), [reloadProduction]);

  const visibleProduction = production.filter((item) => visibility === "all" || item.visibility === visibility);

  return (
    <AppShell>
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Repositorio institucional</p>
          <h2 className="mt-2 text-3xl font-semibold">Producción científica</h2>
        </div>
        <button className="focus-ring rounded-[8px] bg-ink px-4 py-3 text-sm font-semibold text-white">
          Registrar producto
        </button>
      </header>
      <section className="mt-6 grid gap-3 md:grid-cols-4">
        {[
          ["Detectados", detectedCount],
          ["Elegibles KPI", eligibleCount],
          ["Pendientes", pendingCount],
          ["Descartados", discardedCount]
        ].map(([label, value]) => (
          <div key={label} className="rounded-[8px] border border-line bg-white px-4 py-3 shadow-soft">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/50">{label}</p>
            <p className="mt-2 text-2xl font-semibold text-ink">{value}</p>
          </div>
        ))}
      </section>
      <div className="mt-5 inline-flex max-w-full gap-1 overflow-x-auto rounded-[8px] border border-line bg-white p-1 shadow-soft" role="group" aria-label="Filtrar producción">
        {([
          ["all", "Todos"],
          ["eligible", "Elegibles"],
          ["pending", "Pendientes"],
          ["discarded", "Descartados"]
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setVisibility(value)}
            className={`focus-ring min-w-fit rounded-[6px] px-3 py-2 text-sm font-semibold ${visibility === value ? "bg-ink text-white" : "text-ink/65 hover:bg-paper"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="mt-3 text-sm text-ink/60">Visibles en tabla: {visibleProduction.length}</p>
      <section className="mt-6">
        <DataTable
          rows={visibleProduction}
          columns={[
            {
              key: "title",
              label: "Título",
              render: (item) => (
                <div className="max-w-[520px]">
                  <p className="font-medium text-ink">{displayProductTitle(item)}</p>
                  {isTruncatedProduct(item) ? <p className="mt-1 text-xs font-semibold text-amber-700">Pendiente por título truncado</p> : null}
                </div>
              )
            },
            { key: "production_type", label: "Tipo", render: (item) => labelMap[item.production_type] },
            { key: "teacher_name", label: "Docente responsable", render: (item) => item.teacher_name ?? "Pendiente de validación" },
            {
              key: "kpi_status",
              label: "Estado KPI",
              render: (item) => (
                <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${productBadgeClass(item)}`}>
                  {productStatusLabel(item)}
                </span>
              )
            },
            {
              key: "validation_status",
              label: "Validación",
              render: (item) => validationLabel[productValidationStatus(item)] ?? productValidationStatus(item)
            },
            { key: "journal", label: "Revista" },
            { key: "quartile", label: "Cuartil" },
            {
              key: "evidence_url",
              label: "Evidencia",
              render: (item) => (item.evidence_status === "available" ? "Disponible" : "Pendiente")
            }
          ]}
          renderExpanded={(item) => (
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-ink">Detalle del registro</h3>
                <div className="mt-3 space-y-2 text-sm text-ink/70">
                  <p>Tipo: {labelMap[item.production_type]}</p>
                  <p>Periodo: {item.year_label}, ciclo {item.cycle}</p>
                  <p>Validación: {validationLabel[productValidationStatus(item)] ?? productValidationStatus(item)}</p>
                  <p>Estado KPI: {productStatusLabel(item)}</p>
                  <p>Motivo: {item.review_reason || item.normalization_reason || "Sin observaciones de validacion"}</p>
                  {rawProductText(item) ? (
                    <p>Texto crudo: {rawProductText(item)}</p>
                  ) : null}
                  {item.normalized_authors ? <p>Autores normalizados: {item.normalized_authors}</p> : null}
                  <p>Fuente: {item.document || item.source_file || "No registrada"}{item.source_section ? ` / ${item.source_section}` : ""}</p>
                  <p>Página: {item.source_page ?? "No registrada"}</p>
                  <p>Confianza: {item.confidence == null ? "No registrada" : `${Math.round(item.confidence * 100)}%`}</p>
                  <p>Cuartil: {item.quartile ?? "No aplica"}</p>
                  <p>Revista: {item.journal || "No registrada"}</p>
                </div>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-ink">Autores canónicos</h3>
                <div className="mt-3 space-y-2 text-sm text-ink/70">
                  {item.authors?.length ? item.authors.map((author) => (
                    <div key={author.id} className="rounded-[8px] border border-line bg-white p-3">
                      <p className="font-medium text-ink">{author.canonical_name}</p>
                      <p className="mt-1 text-xs text-ink/55">{author.validation_status}</p>
                      <p className="mt-1 text-xs text-ink/55">Variantes: {author.variants.join(", ")}</p>
                      {author.identity_reason ? <p className="mt-1 text-xs text-ink/55">{author.identity_reason}</p> : null}
                    </div>
                  )) : <p>Sin autores detectados</p>}
                  <p>Facultad: {item.faculty_name ?? "No registrada"}</p>
                  <p>Carrera: {item.career_name ?? "No registrada"}</p>
                  <p>Enlace: {item.link || "No registrado"}</p>
                  <p>Evidencia: {item.evidence_url || "Pendiente"}</p>
                </div>
              </div>
            </div>
          )}
        />
      </section>
    </AppShell>
  );
}

export default function ProductionPage() {
  return (
    <Suspense fallback={null}>
      <LegacyProductionPage />
    </Suspense>
  );
}
