import type {
  CaseDetail,
  DecisionDraft,
  OptimisticConflictSnapshot,
  ReviewCommandResult,
  ReviewRequestState
} from "../../lib/human-review";
import { validateSessionDraft } from "../../lib/human-review";

type SessionConfirmationBarProps = {
  selectedCaseIds: readonly string[];
  draftByCaseId: Record<string, DecisionDraft | undefined>;
  detailByCaseId?: Readonly<Record<string, CaseDetail | undefined>>;
  validationByCaseId: Record<string, string[] | undefined>;
  conflictByCaseId?: Record<string, OptimisticConflictSnapshot | undefined>;
  resultByCaseId: Record<string, ReviewCommandResult | undefined>;
  requestStateByCaseId: Record<string, ReviewRequestState | undefined>;
  onConfirm: () => void;
};

export function SessionConfirmationBar({
  selectedCaseIds,
  draftByCaseId,
  detailByCaseId,
  validationByCaseId,
  conflictByCaseId = {},
  resultByCaseId,
  requestStateByCaseId,
  onConfirm
}: SessionConfirmationBarProps) {
  const counts = selectedCaseIds.reduce((current, caseId) => {
    const state = requestStateByCaseId[caseId] ?? "idle";
    if (resultByCaseId[caseId]?.status === "confirmed") current.confirmed += 1;
    else if (!draftByCaseId[caseId]) return current;
    else if (state === "queued" || state === "sending") current.sending += 1;
    else if ((detailByCaseId
      ? validateSessionDraft(detailByCaseId[caseId], draftByCaseId[caseId], conflictByCaseId[caseId])
      : validationByCaseId[caseId] ?? []).length > 0) current.invalid += 1;
    else current.prepared += 1;
    return current;
  }, { prepared: 0, invalid: 0, sending: 0, confirmed: 0 });
  const blocked = counts.sending > 0 || counts.prepared === 0;

  return (
    <section aria-label="Confirmación de la sesión" className="sticky bottom-0 rounded-[8px] border border-line bg-white p-4 shadow-soft">
      <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm font-semibold text-ink/70">
        <span>{`Preparados ${counts.prepared}`}</span>
        <span>{`Inválidos ${counts.invalid}`}</span>
        <span>{`En envío ${counts.sending}`}</span>
        <span>{`Confirmados ${counts.confirmed}`}</span>
      </div>
      {counts.invalid > 0 ? (
        <p role="alert" className="mt-3 text-sm font-semibold text-coral">
          Completa los campos obligatorios o el motivo indicado en la revisión antes de confirmar.
        </p>
      ) : null}
      <button
        type="button"
        aria-label="Confirmar decisión; confirmar casos preparados"
        disabled={blocked}
        onClick={onConfirm}
        className="focus-ring mt-3 rounded-[8px] bg-ink px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        Confirmar casos preparados
      </button>
    </section>
  );
}
