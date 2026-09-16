import type { ReactNode } from "react";

import type { CaseDetail, PublicScalar, ReviewCaseStatus, ScientificStatus } from "../../lib/human-review";
import type { HumanReviewApiError } from "../../lib/human-review-api";

const unsafePublicValue = /(?:[\u0000-\u001f\u007f]|\b[a-z][a-z0-9+.-]*:\/\/|\b[a-z]+_(?:path|key)\b|(?:^|\s)[a-z]:[\\/]|(?:^|\s)(?:\\\\|\/)[^\s]|\.\.[\\/])/i;

const caseStatusLabels: Record<ReviewCaseStatus, string> = {
  pending: "Pendiente",
  in_review: "En revisi\u00f3n",
  awaiting_gestor_approval: "Esperando aprobaci\u00f3n",
  resolved: "Resuelto",
  reopened: "Reabierto",
  conflicted: "En conflicto",
  superseded: "Reemplazado"
};

const scientificStatusLabels: Record<ScientificStatus, string> = {
  pending: "Pendiente",
  validated: "Validado",
  rejected: "Rechazado",
  discarded: "Descartado"
};

const overrideFieldLabels: Record<string, string> = {
  canonical_identity_key: "Identidad can\u00f3nica",
  canonical_name: "Nombre can\u00f3nico",
  product_title: "T\u00edtulo del producto",
  author_identity_key: "Identidad del autor",
  project_director_identity_key: "Identidad del director",
  project_director_relationship_status: "Relaci\u00f3n con el director",
  external_identity_key: "Identidad externa",
  external_institution: "Instituci\u00f3n externa",
  scientific_status: "Estado cient\u00edfico"
};

const scopeLabels: Record<string, string> = {
  global_identity: "Identidad global",
  record: "Registro",
  document: "Documento",
  relationship: "Relaci\u00f3n",
  period: "Periodo"
};

export function safePublicText(value: PublicScalar | undefined, fallback = "No disponible"): string {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  if (!text || text === "[redacted]" || unsafePublicValue.test(text)) return fallback;
  return text.slice(0, 4000);
}

function formatDate(value: PublicScalar | undefined): string | null {
  if (typeof value !== "string") return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("es-EC", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function LayerCard({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <article className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
      <h4 className="text-sm font-semibold text-ink">{title}</h4>
      <p className="mt-1 text-xs text-ink/50">{note}</p>
      <div className="mt-4 whitespace-pre-wrap break-words text-sm leading-6 text-ink/80">{children}</div>
    </article>
  );
}

function safeErrorMessage(error: HumanReviewApiError): string {
  if (error.status === 403) return "No tienes acceso al detalle de revisi\u00f3n humana.";
  if (error.status === 404) return "El caso solicitado no existe o ya no est\u00e1 disponible.";
  if (error.status === 503) return "El servicio de revisi\u00f3n humana no est\u00e1 disponible temporalmente.";
  return "No pudimos cargar el detalle de revisi\u00f3n humana.";
}

export function DetailStateNotice({
  state,
  error
}: {
  state: "loading" | "denied" | "error";
  error?: HumanReviewApiError | null;
}) {
  if (state === "loading") {
    return <div role="status" aria-live="polite" className="rounded-[8px] border border-line bg-white p-6 text-sm text-ink/60 shadow-soft">Cargando detalle del caso...</div>;
  }
  const message = state === "denied"
    ? "No tienes acceso al detalle de revisi\u00f3n humana."
    : error
      ? safeErrorMessage(error)
      : "No pudimos cargar el detalle de revisi\u00f3n humana.";
  return (
    <div role="alert" className="rounded-[8px] border border-coral/30 bg-white p-5 text-sm shadow-soft">
      <p className="font-semibold text-coral">{message}</p>
      {error?.correlation_id ? <p className="mt-2 text-ink/60">Referencia de soporte: {safePublicText(error.correlation_id, "")}</p> : null}
    </div>
  );
}

export function DetectedDataPanel({ detail }: { detail: CaseDetail }) {
  const memberships = detail.effective_memberships
    .map((membership) => safePublicText(membership, ""))
    .filter(Boolean);
  return (
    <section aria-labelledby="case-data-heading" className="space-y-4">
      <div className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 id="case-data-heading" className="text-lg font-semibold text-ink">{"Capas de informaci\u00f3n"}</h3>
            <p className="mt-1 text-sm text-ink/55">{"Lectura comparativa del dato autom\u00e1tico y su proyecci\u00f3n efectiva."}</p>
          </div>
          <span className="rounded-full bg-grape/25 px-3 py-1 text-xs font-semibold text-ink">{caseStatusLabels[detail.case_status]}</span>
        </div>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          <div><dt className="text-ink/50">{"Estado cient\u00edfico"}</dt><dd className="mt-1 font-semibold text-ink">{scientificStatusLabels[detail.scientific_status]}</dd></div>
          <div><dt className="text-ink/50">{"Decisi\u00f3n vigente"}</dt><dd className="mt-1 font-semibold text-ink">{detail.case_status === "reopened" ? "Caso reabierto \u00b7 editable nuevamente" : detail.current_decision_id ? "Registrada \u00b7 solo lectura" : "Sin decisi\u00f3n vigente"}</dd></div>
          <div><dt className="text-ink/50">Impacto KPI</dt><dd className="mt-1 font-semibold text-ink">{detail.possible_kpi_impact ? "Impacto KPI potencial" : "Sin impacto indicado"}</dd></div>
        </dl>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <LayerCard title={"Informaci\u00f3n detectada"} note={"Extracci\u00f3n autom\u00e1tica; no constituye una decisi\u00f3n definitiva."}>{safePublicText(detail.detected_value)}</LayerCard>
        <LayerCard title={"Informaci\u00f3n normalizada"} note={"Representaci\u00f3n normalizada por el procesamiento autom\u00e1tico."}>{safePublicText(detail.normalized_value)}</LayerCard>
        <LayerCard title={"Valor can\u00f3nico o proyectado"} note="Valor efectivo disponible para lectura validada.">{safePublicText(detail.canonical_value)}</LayerCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
          <h4 className="text-sm font-semibold text-ink">Ajustes aplicados</h4>
          {detail.overrides.length ? (
            <ul className="mt-4 space-y-3">
              {detail.overrides.map((override, index) => {
                const rawField = typeof override.field === "string" ? override.field : typeof override.field_path === "string" ? override.field_path : "";
                const label = overrideFieldLabels[rawField] ?? "Campo revisado";
                const scope = typeof override.scope === "string" ? scopeLabels[override.scope] : undefined;
                const createdAt = formatDate(override.created_at);
                return (
                  <li key={`${label}-${index}`} className="rounded-[8px] bg-paper/65 p-3 text-sm">
                    <p className="font-semibold text-ink">{label}</p>
                    <p className="mt-1 break-words text-ink/75">{safePublicText(override.value)}</p>
                    {scope || createdAt ? <p className="mt-2 text-xs text-ink/50">{[scope, createdAt].filter(Boolean).join(" \u00b7 ")}</p> : null}
                  </li>
                );
              })}
            </ul>
          ) : <p className="mt-4 text-sm text-ink/55">No hay ajustes aplicados.</p>}
        </article>

        <article className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
          <h4 className="text-sm font-semibold text-ink">{"Membres\u00edas efectivas actuales"}</h4>
          <p className="mt-1 text-xs text-ink/50">Derivadas de los datos persistidos actuales; no representan un historial exacto.</p>
          {memberships.length ? (
            <ul className="mt-4 flex flex-wrap gap-2">
              {memberships.map((membership, index) => <li key={`${membership}-${index}`} className="rounded-full bg-mint/20 px-3 py-1 text-sm font-semibold text-ink">{membership}</li>)}
            </ul>
          ) : <p className="mt-4 text-sm text-ink/55">{"No hay membres\u00edas efectivas disponibles."}</p>}
        </article>
      </div>
    </section>
  );
}
