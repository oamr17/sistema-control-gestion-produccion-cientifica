"use client";

import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { AuthenticatedPdfButton } from "@/components/AuthenticatedPdfButton";
import { DataTable } from "@/components/DataTable";
import { api } from "@/lib/api";
import { useCachedQuery } from "@/lib/data-cache";
import type { ImportBatch, ImportJob, ImportStatus } from "@/lib/types";

const statusMap: Record<string, string[]> = {
  queued: ["QUEUED"],
  processing: ["PROCESSING"],
  processed: ["SUCCESS"],
  ignored: ["SKIPPED"],
  failed: ["ERROR"],
  requires_review: ["REQUIRES_REVIEW"]
};

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

function formatMs(value: number | null | undefined) {
  if (value === null || value === undefined) return "Sin dato";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    QUEUED: "En cola",
    PROCESSING: "Procesando",
    SUCCESS: "Procesado",
    SKIPPED: "Ignorado",
    REQUIRES_REVIEW: "Requiere revision",
    ERROR: "Error",
    COMPLETED: "Completado",
    COMPLETED_WITH_ERRORS: "Completado con errores",
    HISTORICAL: "Historico"
  };
  return labels[status] ?? status;
}

function statusClasses(status: string) {
  if (status === "SUCCESS" || status === "COMPLETED") return "bg-mint/10 text-mint";
  if (status === "ERROR" || status === "COMPLETED_WITH_ERRORS") return "bg-coral/10 text-coral";
  if (status === "REQUIRES_REVIEW" || status === "QUEUED") return "bg-amber/25 text-[#705500]";
  if (status === "PROCESSING") return "bg-grape/30 text-ink";
  return "bg-paper text-ink/65";
}

function StatPill({ label, value, href }: { label: string; value: number; href?: string }) {
  const content = (
    <div className="rounded-[8px] border border-line bg-white px-4 py-3 shadow-soft">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">{label}</p>
      <strong className="mt-1 block text-2xl text-ink">{value}</strong>
    </div>
  );
  return href ? <Link href={href}>{content}</Link> : content;
}

function ImportacionesContent() {
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const batchesQuery = useCachedQuery<ImportBatch[]>("imports-batches", () => api.importBatches(), {
    staleTimeMs: 30_000
  });
  const statusQuery = useCachedQuery<ImportStatus>("imports-latest-status", () => api.latestImportStatus(), {
    staleTimeMs: 15_000
  });
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const batches = batchesQuery.data ?? [];
  const latestBatchId = statusQuery.data?.batch_id ?? batches[0]?.id ?? null;
  const activeBatchId = selectedBatchId ?? latestBatchId;
  const jobsQuery = useCachedQuery<ImportJob[]>(
    `imports-jobs:${activeBatchId ?? "all"}`,
    () => (activeBatchId ? api.importBatchJobs(activeBatchId) : api.importJobs()),
    { enabled: Boolean(activeBatchId), staleTimeMs: 15_000 }
  );
  const allowed = status ? statusMap[status] : null;
  const rows = useMemo(
    () => (jobsQuery.data ?? []).filter((job) => !allowed || allowed.includes(job.status)),
    [allowed, jobsQuery.data]
  );
  const currentBatch = batches.find((batch) => batch.id === activeBatchId);
  const summary = statusQuery.data;
  const processed = (summary?.processed ?? 0) + (summary?.ignored ?? 0) + (summary?.requires_review ?? 0) + (summary?.failed ?? 0);

  return (
    <AppShell>
      <header>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Importaciones</p>
        <h2 className="mt-2 text-3xl font-semibold">Lotes de informes PDF</h2>
        <p className="mt-2 text-sm text-ink/60">
          Recepcion desde n8n, procesamiento controlado por archivo y trazabilidad del lote actual.
        </p>
      </header>

      <section className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
        <StatPill label="Total" value={summary?.total ?? 0} href="/importaciones" />
        <StatPill label="En cola" value={summary?.queued ?? 0} href="/importaciones?status=queued" />
        <StatPill label="Procesando" value={summary?.processing ?? 0} href="/importaciones?status=processing" />
        <StatPill label="Procesados" value={summary?.processed ?? 0} href="/importaciones?status=processed" />
        <StatPill label="Ignorados" value={summary?.ignored ?? 0} href="/importaciones?status=ignored" />
        <StatPill label="Revision" value={summary?.requires_review ?? 0} href="/importaciones?status=requires_review" />
        <StatPill label="Errores" value={summary?.failed ?? 0} href="/importaciones?status=failed" />
      </section>

      {summary?.active ? (
        <p className="mt-4 rounded-[8px] border border-line bg-white px-4 py-3 text-sm font-medium text-ink/70 shadow-soft">
          Procesando {processed}/{summary.total} informes del lote #{summary.batch_id}.
        </p>
      ) : null}

      <section className="mt-6 grid gap-5 xl:grid-cols-[0.35fr_0.65fr]">
        <aside className="min-w-0 rounded-[8px] border border-line bg-white p-5 shadow-soft">
          <h3 className="text-lg font-semibold">Historial de lotes</h3>
          <div className="mt-4 space-y-3">
            {batchesQuery.isInitialLoading ? <p className="text-sm text-ink/55">Cargando lotes...</p> : null}
            {batches.map((batch) => (
              <button
                key={batch.id}
                type="button"
                onClick={() => setSelectedBatchId(batch.id)}
                className={`w-full rounded-[8px] border px-4 py-3 text-left transition ${
                  activeBatchId === batch.id ? "border-mint bg-mint/10" : "border-line hover:bg-paper/70"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold">Lote #{batch.id}</span>
                  <span className={`rounded-full px-2 py-1 text-xs font-semibold ${statusClasses(batch.status)}`}>
                    {statusLabel(batch.status)}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink/55">{formatDate(batch.created_at)}</p>
                <p className="mt-2 text-sm text-ink/65">{batch.total_files} archivo(s)</p>
              </button>
            ))}
            {!batchesQuery.isInitialLoading && !batches.length ? (
              <p className="rounded-[8px] bg-paper p-4 text-sm text-ink/55">Sin lotes registrados.</p>
            ) : null}
          </div>
        </aside>

        <section className="min-w-0">
          <div className="mb-3 rounded-[8px] border border-line bg-white p-4 shadow-soft">
            <h3 className="text-lg font-semibold">
              {currentBatch ? `Archivos del lote #${currentBatch.id}` : "Archivos del lote actual"}
            </h3>
            <p className="mt-1 text-sm text-ink/60">
              {status ? `Filtro activo: ${statusLabel(statusMap[status]?.[0] ?? status)}` : "Mostrando todos los estados."}
            </p>
          </div>

          {jobsQuery.isInitialLoading ? (
            <p className="rounded-[8px] border border-line bg-white p-4 text-sm text-ink/60 shadow-soft">
              Cargando archivos del lote...
            </p>
          ) : (
            <DataTable
              rows={rows}
              columns={[
                { key: "id", label: "ID" },
                { key: "filename", label: "Archivo" },
                {
                  key: "status",
                  label: "Estado",
                  render: (job) => (
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${statusClasses(job.status)}`}>
                      {statusLabel(job.status)}
                    </span>
                  )
                },
                { key: "retry_count", label: "Reintentos" },
                { key: "created_at", label: "Recibido", render: (job) => formatDate(job.created_at) },
                { key: "processed_at", label: "Procesado", render: (job) => formatDate(job.processed_at) },
                {
                  key: "pdf",
                  label: "PDF",
                  render: (job) => (
                    <AuthenticatedPdfButton
                      load={(options) => api.importJobFile(job.id, options)}
                      className="focus-ring inline-flex rounded-[8px] border border-line bg-white px-3 py-2 text-xs font-semibold text-mint transition hover:border-mint hover:bg-mint/10"
                    >
                      Ver PDF
                    </AuthenticatedPdfButton>
                  )
                }
              ]}
              renderExpanded={(job) => (
                <div className="space-y-4 text-sm text-ink/70">
                  <div className="flex flex-col gap-3 rounded-[8px] border border-line bg-white p-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="font-semibold text-ink">PDF importado</p>
                      <p className="mt-1 text-xs text-ink/55">{job.filename}</p>
                    </div>
                    <AuthenticatedPdfButton
                      load={(options) => api.importJobFile(job.id, options)}
                      className="focus-ring inline-flex w-fit rounded-[8px] bg-mint px-4 py-2 text-sm font-semibold text-white transition hover:bg-mint/90"
                    >
                      Abrir PDF importado
                    </AuthenticatedPdfButton>
                  </div>
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    <p>Batch: {job.batch_id ?? "Historico"}</p>
                    <p>Importado por: {job.imported_by ?? "No registrado"}</p>
                    <p>Revision Dropbox: {job.source_rev ?? "Sin rev"}</p>
                    <p>Paso actual: {job.current_step ?? "Sin paso registrado"}</p>
                    <p>Tipo de error: {job.error_type ?? "Sin tipo"}</p>
                    <p>
                      Reintentos: {job.retry_count}/{job.max_retries}
                    </p>
                    <p>Duracion total: {formatMs(job.duration_ms)}</p>
                    <p>Tiempo en cola: {formatMs(job.queue_ms)}</p>
                    <p>Descarga: {formatMs(job.download_ms)}</p>
                    <p>Extraccion texto: {formatMs(job.text_extraction_ms)}</p>
                    <p>OCR: {formatMs(job.ocr_ms)}</p>
                    <p>Parser: {formatMs(job.parser_ms)}</p>
                    <p>Persistencia DB: {formatMs(job.persistence_ms)}</p>
                    <p>Paginas: {job.page_count ?? "Sin dato"}</p>
                    <p>Uso OCR: {job.used_ocr ? "Si" : "No"}</p>
                    <p>Metodo: {job.extraction_method ?? "Sin dato"}</p>
                  </div>
                  <div>
                    <p className="font-semibold text-ink">Observación</p>
                    <p className="mt-1 whitespace-pre-wrap rounded-[8px] border border-line bg-paper/70 p-3">
                      {job.status === "ERROR"
                        ? "No se pudo completar la importación. Consulte los registros del servidor para el diagnóstico técnico."
                        : job.error_message ?? job.error_reason ?? job.summary ?? "Sin observaciones"}
                    </p>
                  </div>
                </div>
              )}
            />
          )}
        </section>
      </section>
    </AppShell>
  );
}

export default function ImportacionesPage() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <p className="rounded-[8px] border border-line bg-white p-4 text-sm text-ink/60 shadow-soft">
            Cargando importaciones...
          </p>
        </AppShell>
      }
    >
      <ImportacionesContent />
    </Suspense>
  );
}
