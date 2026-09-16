import Link from "next/link";

import type { HumanReviewApiError } from "../../lib/human-review-api";
import type { QueueItem, QueueResponse, ReviewCaseStatus, ReviewCaseType } from "../../lib/human-review";

const caseTypeLabels: Record<ReviewCaseType, string> = {
  person_identity: "Identidad de persona",
  author_identity: "Identidad de autor",
  product: "Producción científica",
  project_director_relation: "Relaci\u00f3n con director",
  external_identity: "Identidad externa",
  possible_duplicate: "Posible duplicado",
  invalid_text: "Texto inv\u00e1lido",
  new_evidence_conflict: "Conflicto de evidencia"
};

const statusLabels: Record<ReviewCaseStatus, string> = {
  pending: "Pendiente",
  in_review: "En revisi\u00f3n",
  awaiting_gestor_approval: "Esperando aprobaci\u00f3n",
  resolved: "Resuelto",
  reopened: "Reabierto",
  conflicted: "En conflicto",
  superseded: "Reemplazado"
};

const reviewReasonLabels: Record<ReviewCaseType, string> = {
  person_identity: "Confirmar la identidad de la persona detectada.",
  author_identity: "Confirmar la identidad del autor detectado.",
  product: "Validar los datos de la producción científica.",
  project_director_relation: "Confirmar la relación entre el proyecto y su director.",
  external_identity: "Validar la identidad externa detectada.",
  possible_duplicate: "Determinar si los registros corresponden a la misma entidad.",
  invalid_text: "Corregir el texto que no pudo validarse automáticamente.",
  new_evidence_conflict: "Resolver la diferencia entre la decisión vigente y la nueva evidencia."
};

function safeErrorMessage(error: HumanReviewApiError) {
  if (error.status === 403) return "No tienes acceso a la bandeja de revisi\u00f3n humana.";
  if (error.status === 503) return "La bandeja de revisi\u00f3n humana no est\u00e1 disponible temporalmente.";
  return "No pudimos cargar la bandeja de revisi\u00f3n humana.";
}

function formatCreatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-EC", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function priorityLabel(item: QueueItem) {
  return item.manual_priority === null
    ? `Autom\u00e1tica ${item.automatic_priority}`
    : `Manual ${item.manual_priority} \u00b7 autom\u00e1tica ${item.automatic_priority}`;
}

function firstAvailableValue(...values: (string | null | undefined)[]) {
  return values.find((value) => typeof value === "string" && value.trim().length > 0)?.trim() ?? null;
}

function ErrorNotice({ error }: { error: HumanReviewApiError }) {
  return (
    <div role="alert" className="rounded-[8px] border border-coral/30 bg-white p-4 text-sm shadow-soft">
      <p className="font-semibold text-coral">{safeErrorMessage(error)}</p>
      {error.correlation_id ? <p className="mt-2 text-ink/60">Referencia de soporte: {error.correlation_id}</p> : null}
    </div>
  );
}

export function ReviewQueueTable({
  data,
  error = null,
  isInitialLoading,
  isUpdating,
  hasActiveFilters = false
}: {
  data?: Pick<QueueResponse, "items" | "total">;
  error?: HumanReviewApiError | null;
  isInitialLoading: boolean;
  isUpdating: boolean;
  hasActiveFilters?: boolean;
}) {
  if (isInitialLoading) {
    return <div role="status" aria-live="polite" className="rounded-[8px] border border-line bg-white p-6 text-sm text-ink/60 shadow-soft">{"Cargando casos de revisi\u00f3n humana..."}</div>;
  }
  if (error && !data) return <ErrorNotice error={error} />;

  return (
    <div className="space-y-3">
      {error ? <ErrorNotice error={error} /> : null}
      {isUpdating ? <p role="status" aria-live="polite" className="text-sm font-semibold text-mint">Actualizando casos...</p> : null}
      {!data?.items.length ? (
        <div className="rounded-[8px] border border-line bg-white p-6 text-sm text-ink/60 shadow-soft">
          {hasActiveFilters ? "No se encontraron casos con los filtros seleccionados." : "No hay casos de revisi\u00f3n humana en la bandeja."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[8px] border border-line bg-white shadow-soft">
          <table className="min-w-[1060px] divide-y divide-line text-sm">
            <caption className="sr-only">{"Bandeja de casos de revisi\u00f3n humana ordenada por prioridad y antig\u00fcedad"}</caption>
            <thead className="bg-paper/70">
              <tr>
                {['Caso', 'Tipo', 'Estado', 'Origen', 'Prioridad', 'KPI', 'Creado', 'Acciones'].map((label) => <th key={label} scope="col" className="px-4 py-3 text-left font-semibold text-ink/70">{label}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.items.map((item) => {
                const reviewValue = firstAvailableValue(item.detected_value, item.normalized_value, item.canonical_value);
                return (
                  <tr key={item.id} className="align-top">
                    <td className="max-w-[240px] px-4 py-4 text-ink">
                      <span className="block font-semibold leading-5">{reviewValue ?? caseTypeLabels[item.case_type]}</span>
                      {item.normalized_value && item.normalized_value !== reviewValue ? (
                        <span className="mt-1 block text-xs text-ink/60">Normalizado: {item.normalized_value}</span>
                      ) : null}
                      {item.canonical_value && item.canonical_value !== reviewValue ? (
                        <span className="mt-1 block text-xs text-ink/60">Actual: {item.canonical_value}</span>
                      ) : null}
                      <span className="mt-2 block text-xs text-ink/60">{reviewReasonLabels[item.case_type]}</span>
                      <details className="mt-2 text-xs text-ink/50">
                        <summary className="cursor-pointer font-semibold">Ver referencia pública del caso</summary>
                        <span className="mt-1 block break-all font-mono">{item.id}</span>
                      </details>
                    </td>
                    <td className="px-4 py-4 text-ink/75">{caseTypeLabels[item.case_type]}</td>
                    <td className="px-4 py-4"><span className="rounded-full bg-grape/25 px-2 py-1 text-xs font-semibold text-ink">{statusLabels[item.case_status]}</span></td>
                    <td className="px-4 py-4 text-ink/75">
                      <span className="block">{item.document_name ?? "Documento no disponible"}</span>
                      <span className="mt-1 block text-xs text-ink/55">{"p\u00e1g."} {item.source_page ?? "—"}</span>
                      <span className="mt-1 block max-w-xs text-xs text-ink/55">{item.source_section || "Secci\u00f3n no disponible"}</span>
                    </td>
                    <td className="px-4 py-4 text-ink/75">{priorityLabel(item)}</td>
                    <td className="px-4 py-4">{item.possible_kpi_impact ? <span className="rounded-full bg-amber/30 px-2 py-1 text-xs font-semibold text-ink">Impacto potencial</span> : <span className="text-ink/50">Sin impacto indicado</span>}</td>
                    <td className="px-4 py-4 text-ink/75">{formatCreatedAt(item.created_at)}</td>
                    <td className="px-4 py-4">
                      <Link
                        href={`/human-review/cases/${encodeURIComponent(item.id)}`}
                        aria-label={`Revisar ${caseTypeLabels[item.case_type].toLocaleLowerCase("es-EC")}: ${reviewValue ?? "registro pendiente de validación"}`}
                        className="focus-ring inline-flex whitespace-nowrap rounded-[8px] border border-line px-3 py-2 text-sm font-semibold text-ink hover:bg-paper"
                      >
                        Revisar caso
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
