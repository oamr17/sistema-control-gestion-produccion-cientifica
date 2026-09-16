import type { AuditItem, AuditTimelineResponse, PublicScalar } from "../../lib/human-review";
import type { HumanReviewApiError } from "../../lib/human-review-api";
import { safePublicText } from "./DetectedDataPanel";

const eventLabels: Record<string, string> = {
  case_backfilled: "Caso incorporado al historial",
  locked_decision_imported: "Decisi\u00f3n bloqueada importada",
  identity_created: "Identidad creada",
  alias_created: "Alias creado",
  override_created: "Ajuste aplicado",
  capability_assigned: "Capacidad asignada",
  capability_revoked: "Capacidad revocada",
  audit_corrected: "Auditor\u00eda corregida",
  functional_reversion: "Reversi\u00f3n funcional",
  scientific_decision_applied: "Decisi\u00f3n cient\u00edfica aplicada",
};

const decisionLabels: Record<string, string> = {
  validated: "Validaci\u00f3n",
  corrected: "Correcci\u00f3n",
  linked: "Vinculaci\u00f3n",
  merged: "Fusi\u00f3n",
  maintained_separate: "Mantenido separado",
  separated: "Separaci\u00f3n",
  rejected: "Rechazo",
  discarded: "Descarte",
  maintained: "Mantenido",
  reverted: "Reversi\u00f3n"
};

const statusLabels: Record<string, string> = {
  pending: "Pendiente",
  in_review: "En revisi\u00f3n",
  awaiting_gestor_approval: "Esperando aprobaci\u00f3n",
  resolved: "Resuelto",
  reopened: "Reabierto",
  conflicted: "En conflicto",
  superseded: "Reemplazado"
};

function itemTime(item: AuditItem): number {
  if (typeof item.created_at !== "string") return Number.MAX_SAFE_INTEGER;
  const time = new Date(item.created_at).getTime();
  return Number.isNaN(time) ? Number.MAX_SAFE_INTEGER : time;
}

export function ascendingAuditItems(items: AuditItem[]): AuditItem[] {
  return items.map((item, index) => ({ item, index }))
    .sort((left, right) => itemTime(left.item) - itemTime(right.item) || left.index - right.index)
    .map(({ item }) => item);
}

function formatDate(value: PublicScalar | undefined): string {
  if (typeof value !== "string") return "Fecha no disponible";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-EC", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function mappedLabel(value: PublicScalar | undefined, labels: Record<string, string>, fallback: string): string {
  return typeof value === "string" ? labels[value] ?? fallback : fallback;
}

function AuditError({ error }: { error: HumanReviewApiError }) {
  const message = error.status === 403
    ? "No tienes acceso al historial de auditor\u00eda."
    : error.status === 503
      ? "El historial no est\u00e1 disponible temporalmente."
      : "No pudimos cargar el historial de auditor\u00eda.";
  return (
    <div role="alert" className="rounded-[8px] border border-coral/30 p-4 text-sm">
      <p className="font-semibold text-coral">{message}</p>
      {error.correlation_id ? <p className="mt-1 text-ink/55">Referencia de soporte: {safePublicText(error.correlation_id, "")}</p> : null}
    </div>
  );
}

export function AuditTimeline({
  response,
  error = null,
  isInitialLoading,
  onPageChange
}: {
  response?: AuditTimelineResponse;
  error?: HumanReviewApiError | null;
  isInitialLoading: boolean;
  onPageChange: (page: number) => void;
}) {
  if (isInitialLoading) return <div role="status" aria-live="polite" className="rounded-[8px] border border-line bg-white p-5 text-sm text-ink/60 shadow-soft">{"Cargando historial de auditor\u00eda..."}</div>;
  const items = ascendingAuditItems(response?.items ?? []);
  const page = response?.page ?? 1;
  const pageSize = response?.page_size ?? 25;
  const total = response?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section aria-labelledby="audit-heading" className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="audit-heading" className="text-lg font-semibold text-ink">{"Historial de auditor\u00eda"}</h3>
          <p className="mt-1 text-sm text-ink/55">{"Eventos conservados en orden cronol\u00f3gico ascendente."}</p>
        </div>
        {response ? <p className="text-xs text-ink/50">{total} eventos</p> : null}
      </div>
      {error ? <div className="mt-4"><AuditError error={error} /></div> : null}
      {!error && response && !items.length ? <p className="mt-5 text-sm text-ink/60">{"No hay eventos de auditor\u00eda registrados."}</p> : items.length ? (
        <ol className="mt-5 space-y-4 border-l border-line pl-5">
          {items.map((item, index) => {
            const event = mappedLabel(item.event_type, eventLabels, "Evento de auditor\u00eda");
            const summary = safePublicText(item.summary, "");
            const decisionType = mappedLabel(item.payload?.decision_type, decisionLabels, "");
            const previous = mappedLabel(item.payload?.previous_case_status, statusLabels, "");
            const resulting = mappedLabel(item.payload?.resulting_case_status, statusLabels, "");
            const correlation = safePublicText(item.correlation_id, "");
            return (
              <li key={`${typeof item.id === "number" ? item.id : "event"}-${index}`} className="relative rounded-[8px] bg-paper/60 p-4">
                <span className="absolute -left-[1.55rem] top-5 h-3 w-3 rounded-full border-2 border-white bg-mint" aria-hidden="true" />
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <p className="font-semibold text-ink">{event}</p>
                  <time className="text-xs text-ink/50">{formatDate(item.created_at)}</time>
                </div>
                {summary ? <p className="mt-2 text-sm leading-6 text-ink/75">{summary}</p> : null}
                {decisionType ? <p className="mt-2 text-xs text-ink/60">{"Decisi\u00f3n:"} {decisionType}</p> : null}
                {previous || resulting ? <p className="mt-1 text-xs text-ink/60">Estado: {previous || "No disponible"} {"\u2192"} {resulting || "No disponible"}</p> : null}
                {correlation ? <p className="mt-2 text-xs text-ink/50">Referencia: {correlation}</p> : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      {totalPages > 1 ? (
        <div className="mt-5 flex items-center justify-end gap-2 text-sm">
          <button type="button" onClick={() => onPageChange(page - 1)} disabled={page <= 1} className="focus-ring rounded-[8px] border border-line px-3 py-2 font-semibold disabled:opacity-45">Anterior</button>
          <span className="text-ink/55">{"P\u00e1gina"} {page} de {totalPages}</span>
          <button type="button" onClick={() => onPageChange(page + 1)} disabled={page >= totalPages} className="focus-ring rounded-[8px] border border-line px-3 py-2 font-semibold disabled:opacity-45">Siguiente</button>
        </div>
      ) : null}
    </section>
  );
}
