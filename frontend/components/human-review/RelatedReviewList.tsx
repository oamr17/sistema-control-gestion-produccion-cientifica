import type {
  RelatedReviewItem,
  ReviewCommandResult,
  ReviewRequestState
} from "../../lib/human-review";
import { safePublicText } from "./DetectedDataPanel";
import { ReviewResultSummary } from "./ReviewResultSummary";

const caseTypeLabels: Record<RelatedReviewItem["case_type"], string> = {
  person_identity: "Identidad de persona",
  author_identity: "Identidad de autor",
  product: "Producción científica",
  project_director_relation: "Relación con director",
  external_identity: "Identidad externa",
  possible_duplicate: "Posible duplicado",
  invalid_text: "Texto por revisar",
  new_evidence_conflict: "Nueva evidencia en conflicto"
};

const caseStatusLabels: Record<RelatedReviewItem["case_status"], string> = {
  pending: "Pendiente",
  in_review: "En revisión",
  awaiting_gestor_approval: "Esperando aprobación",
  resolved: "Confirmado",
  reopened: "Reabierto",
  conflicted: "En conflicto",
  superseded: "Reemplazado"
};

const reviewReasonLabels: Record<RelatedReviewItem["case_type"], string> = {
  person_identity: "Confirmar la identidad de la persona detectada.",
  author_identity: "Confirmar la identidad del autor detectado.",
  product: "Validar los datos de la producción científica.",
  project_director_relation: "Confirmar la relación entre el proyecto y su director.",
  external_identity: "Validar la identidad externa detectada.",
  possible_duplicate: "Determinar si los registros corresponden a la misma entidad.",
  invalid_text: "Corregir el texto que no pudo validarse automáticamente.",
  new_evidence_conflict: "Resolver la diferencia entre la decisión vigente y la nueva evidencia."
};

const requestStateLabels: Record<ReviewRequestState, string> = {
  idle: "Listo para preparar",
  queued: "En cola",
  sending: "En envío",
  succeeded: "Confirmado",
  failed: "Requiere atención"
};

type RelatedReviewListProps = {
  items: RelatedReviewItem[];
  activeCaseId: string;
  selectedCaseIds: readonly string[];
  resultByCaseId: Record<string, ReviewCommandResult | undefined>;
  requestStateByCaseId: Record<string, ReviewRequestState | undefined>;
  onSelect: (caseId: string, interaction?: "pointer" | "keyboard") => void;
  onToggleSelected: (caseId: string, selected: boolean) => void;
};

export function RelatedReviewList({
  items,
  activeCaseId,
  selectedCaseIds,
  resultByCaseId,
  requestStateByCaseId,
  onSelect,
  onToggleSelected
}: RelatedReviewListProps) {
  return (
    <section aria-labelledby="related-review-heading" className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
      <h3 id="related-review-heading" className="font-semibold text-ink">Revisiones relacionadas</h3>
      <ul className="mt-3 space-y-3">
        {items.map((item) => {
          const active = item.case_id === activeCaseId;
          const resolved = item.case_status === "resolved" || resultByCaseId[item.case_id]?.status === "confirmed";
          const expanded = active && !resolved;
          const selected = selectedCaseIds.includes(item.case_id);
          const requestState = requestStateByCaseId[item.case_id] ?? "idle";
          const primaryValue = safePublicText(
            item.detected_value ?? item.normalized_value ?? item.canonical_value
          );
          const documentName = safePublicText(item.evidence_summary.document_name, "");
          const page = safePublicText(item.evidence_summary.page, "");
          const section = safePublicText(item.evidence_summary.section, "");
          const sourceContext = [
            documentName,
            page ? `pág. ${page}` : "",
            section
          ].filter(Boolean).join(" · ");
          return (
            <li key={item.case_id} className="rounded-[8px] border border-line p-3">
              <button
                type="button"
                aria-expanded={expanded}
                aria-current={active ? "true" : undefined}
                onClick={(event) => onSelect(item.case_id, event?.detail === 0 ? "keyboard" : "pointer")}
                className="focus-ring flex w-full items-start justify-between gap-3 text-left"
              >
                <span>
                  <span className="block font-semibold text-ink">{caseTypeLabels[item.case_type]}</span>
                  <span className="mt-1 block break-words text-sm text-ink/75">{primaryValue}</span>
                  {sourceContext ? <span className="mt-1 block break-words text-xs text-ink/55">{sourceContext}</span> : null}
                  <span className="mt-1 block break-words text-xs text-ink/60">{reviewReasonLabels[item.case_type]}</span>
                </span>
                <span className="shrink-0 rounded-full bg-paper px-2.5 py-1 text-xs font-semibold text-ink/65">{caseStatusLabels[item.case_status]}</span>
              </button>

              {expanded ? (
                <div className="mt-3 border-t border-line pt-3 text-sm">
                  <p className="text-ink/55">Valor que quedará disponible</p>
                  <p className="mt-1 break-words font-semibold text-ink">{safePublicText(item.canonical_value)}</p>
                  <details className="mt-3 text-xs text-ink/55">
                    <summary className="cursor-pointer font-semibold">Ver referencia pública del caso</summary>
                    <p className="mt-1 break-all">{safePublicText(item.case_id)}</p>
                  </details>
                </div>
              ) : null}

              <div className="mt-3 flex items-center justify-between gap-3 text-xs text-ink/55">
                <label className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={resolved || requestState === "queued" || requestState === "sending"}
                    onChange={(event) => onToggleSelected(item.case_id, event.target.checked)}
                  />
                  {resolved ? "Confirmada · solo lectura" : "Incluir en la sesión"}
                </label>
                <span>{requestStateLabels[requestState]}</span>
              </div>
              {resultByCaseId[item.case_id] ? <div className="mt-3"><ReviewResultSummary result={resultByCaseId[item.case_id]!} /></div> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
