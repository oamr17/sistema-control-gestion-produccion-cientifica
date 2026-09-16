import { Eye } from "lucide-react";

import { buildDecisionPreview, type CaseDetail, type DecisionDraft } from "../../lib/human-review";
import { safePublicText } from "./DetectedDataPanel";

const actionLabels = {
  approve: "Aprobar",
  correct: "Corregir",
  link: "Vincular"
} as const;

const scopeLabels = {
  global_identity: "Identidad global",
  record: "Registro",
  document: "Documento",
  relationship: "Relación",
  period: "Periodo"
} as const;

export function DecisionPreview({ detail, draft }: { detail: CaseDetail; draft: DecisionDraft }) {
  const preview = buildDecisionPreview(detail, draft);
  return (
    <section aria-labelledby="decision-preview-heading" className="rounded-[8px] border border-mint/45 bg-mint/10 p-5">
      <div className="flex items-start gap-3">
        <span className="rounded-full bg-mint/25 p-2 text-ink"><Eye size={17} aria-hidden="true" /></span>
        <div>
          <h4 id="decision-preview-heading" className="font-semibold text-ink">Vista previa local</h4>
          <p className="mt-1 text-xs leading-5 text-ink/55">Es una comparación visual. El servidor validará el resultado definitivo al confirmar.</p>
        </div>
      </div>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
        <div className="rounded-[8px] bg-white/80 p-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-ink/45">Valor actual</dt>
          <dd className="mt-1 break-words text-ink/80">{safePublicText(preview.currentValue)}</dd>
        </div>
        <div className="rounded-[8px] bg-white/80 p-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-ink/45">Valor propuesto</dt>
          <dd className="mt-1 break-words text-ink/80">{safePublicText(preview.proposedValue)}</dd>
        </div>
        <div>
          <dt className="text-ink/50">Acción que se solicitará</dt>
          <dd className="mt-1 font-semibold text-ink">{actionLabels[preview.action]}</dd>
        </div>
        <div>
          <dt className="text-ink/50">Ámbito</dt>
          <dd className="mt-1 font-semibold text-ink">{scopeLabels[preview.scope]}</dd>
        </div>
        <div>
          <dt className="text-ink/50">Posible efecto KPI</dt>
          <dd className="mt-1 font-semibold text-ink">{preview.possibleKpiImpact ? "Puede afectar indicadores" : "Sin impacto indicado"}</dd>
        </div>
        {preview.selectedLink ? (
          <div>
            <dt className="text-ink/50">Vínculo seleccionado</dt>
            <dd className="mt-1 break-words font-semibold text-ink">{safePublicText(preview.selectedLink)}</dd>
          </div>
        ) : null}
      </dl>
      <div className="mt-4 rounded-[8px] bg-white/70 p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">Detalles propuestos</p>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          {preview.proposedFields.map((field) => (
            <div key={field.label}>
              <dt className="text-ink/50">{field.label}</dt>
              <dd className="mt-1 break-words font-semibold text-ink/80">{safePublicText(field.value)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">Campos modificados</p>
        {preview.changedFields.length ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {preview.changedFields.map((field) => <li key={field} className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink/70">{field}</li>)}
          </ul>
        ) : <p className="mt-2 text-sm text-ink/55">No se detectan cambios visuales en el valor principal.</p>}
      </div>
    </section>
  );
}
