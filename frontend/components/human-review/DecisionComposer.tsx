"use client";

import { useEffect, useState } from "react";
import { Check, Link2, Pencil, RotateCcw, Trash2, X } from "lucide-react";

import { useHumanReviewCommands } from "../../hooks/useHumanReview";
import {
  MAX_DECISION_REASON_LENGTH,
  allowedDecisionActions,
  buildApplyDecisionRequest,
  buildDiscardRequest,
  buildRevertRequest,
  cancelDecisionDraft,
  createOptimisticConflictSnapshot,
  createDecisionDraft,
  DecisionValidationError,
  applyOptimisticConflictReload,
  abandonOptimisticConflictOperation,
  isOptimisticVersionConflict,
  isOptimisticConfirmationBlocked,
  isEditableReviewStatus,
  keepOptimisticConflictAfterReloadFailure,
  keepOptimisticConflictAwaitingReload,
  updateOptimisticConflictDraft,
  type CaseDetail,
  type Capabilities,
  type DecisionAction,
  type DecisionDraft,
  type DecisionPayload,
  type LinkResolution,
  withDecisionAction,
  withDecisionPayload
} from "../../lib/human-review";
import { HumanReviewApiError } from "../../lib/human-review-api";
import { ApiFetchError } from "../../lib/api";
import { DecisionPreview } from "./DecisionPreview";
import { DiscardConfirmDialog } from "./DiscardConfirmDialog";
import { IdentityLinker } from "./IdentityLinker";
import { CapabilityGate } from "./CapabilityGate";
import { OptimisticConflictDialog } from "./OptimisticConflictDialog";
import { RevertConfirmDialog } from "./RevertConfirmDialog";
import { safePublicText } from "./DetectedDataPanel";

const actionLabels: Record<DecisionAction, string> = { approve: "Aprobar", correct: "Corregir", link: "Vincular" };
const actionIcons = { approve: Check, correct: Pencil, link: Link2 } as const;
const inputClass = "focus-ring mt-1 w-full rounded-[8px] border border-line bg-white px-3 py-2 text-sm text-ink disabled:cursor-not-allowed disabled:bg-paper/70";
const relationshipStatuses: { value: LinkResolution; label: string }[] = [
  { value: "linked", label: "Vinculado" },
  { value: "maintained_separate", label: "Mantener separado" },
  { value: "separated", label: "Separado" }
];

export type DecisionPayloadField =
  | "canonical_identity_key"
  | "canonical_name"
  | "aliases"
  | "product_title"
  | "project_director_identity_key"
  | "relationship_status"
  | "external_identity_key"
  | "external_institution"
  | "counterpart_ref"
  | "resolution";

export type DecisionPayloadFieldErrors = Partial<Record<DecisionPayloadField, string>>;

function correlationId(): string {
  if (!globalThis.crypto?.randomUUID) throw new DecisionValidationError("No fue posible preparar una referencia segura para la solicitud.");
  return globalThis.crypto.randomUUID();
}

function safeCommandMessage(error: unknown): string {
  if (error instanceof DecisionValidationError) return error.message;
  if (error instanceof HumanReviewApiError) {
    if (error.status === 403) return "No tienes autoridad para confirmar esta decisión.";
    if (error.status === 404) return "El caso ya no está disponible.";
    if (error.status === 409) return "El caso cambió y la decisión no se aplicó.";
    if (error.status === 503) return "El servicio de revisión no está disponible temporalmente.";
  }
  return "No pudimos confirmar la decisión.";
}

function PayloadFieldError({ id, message }: { id: string; message?: string }) {
  return message ? <p id={id} role="alert" className="mt-2 text-sm font-semibold text-coral">{message}</p> : null;
}

function RequiredMarker() {
  return <><span aria-hidden="true" className="text-coral"> *</span><span className="sr-only"> obligatorio</span></>;
}

export function DecisionPayloadFields({
  detail,
  draft,
  disabled,
  fieldErrors = {},
  idPrefix = "decision-payload",
  onChange
}: {
  detail: CaseDetail;
  draft: DecisionDraft;
  disabled: boolean;
  fieldErrors?: DecisionPayloadFieldErrors;
  idPrefix?: string;
  onChange: (payload: DecisionPayload) => void;
}) {
  const payload = draft.payload;
  if (draft.action === "link" && payload.case_type !== "product") {
    return <IdentityLinker payload={payload} counterpartOptions={detail.counterpart_options} disabled={disabled} fieldErrors={fieldErrors} idPrefix={idPrefix} onChange={onChange} />;
  }
  return (
    <fieldset disabled={disabled} className="grid gap-4 sm:grid-cols-2">
      <legend className="sr-only">Datos de la decisión</legend>
      {(payload.case_type === "person_identity" || payload.case_type === "author_identity") ? (
        <>
          <label className="block text-sm font-medium text-ink/75">
            Nombre canónico<RequiredMarker />
            <input value={payload.canonical_name} aria-required="true" aria-invalid={Boolean(fieldErrors.canonical_name)} aria-describedby={fieldErrors.canonical_name ? `${idPrefix}-canonical-name-error` : undefined} onChange={(event) => onChange({ ...payload, canonical_name: event.target.value })} maxLength={500} className={inputClass} />
            <PayloadFieldError id={`${idPrefix}-canonical-name-error`} message={fieldErrors.canonical_name} />
          </label>
          <label className="block text-sm font-medium text-ink/75 sm:col-span-2">
            Alias, separados por coma
            <input value={(payload.aliases ?? []).join(",")} aria-invalid={Boolean(fieldErrors.aliases)} aria-describedby={fieldErrors.aliases ? `${idPrefix}-aliases-error` : undefined} onChange={(event) => onChange({ ...payload, aliases: event.target.value.split(",") })} placeholder="Ej. Ana María, María José" className={inputClass} />
            <span className="mt-1 block text-xs font-normal text-ink/50">Opcional. Separa los alias con comas; puedes incluir espacios dentro de cada nombre.</span>
            <PayloadFieldError id={`${idPrefix}-aliases-error`} message={fieldErrors.aliases} />
          </label>
          <p className="rounded-[8px] border border-line bg-paper/45 p-3 text-sm text-ink/65 sm:col-span-2">La referencia pública de identidad se asignará automáticamente al confirmar.</p>
        </>
      ) : null}
      {payload.case_type === "product" ? (
        <label className="block text-sm font-medium text-ink/75 sm:col-span-2">
          Título del producto
          <input value={payload.product_title ?? ""} aria-invalid={Boolean(fieldErrors.product_title)} aria-describedby={fieldErrors.product_title ? `${idPrefix}-product-title-error` : undefined} onChange={(event) => onChange({ ...payload, product_title: event.target.value || null })} maxLength={1000} className={inputClass} />
          <PayloadFieldError id={`${idPrefix}-product-title-error`} message={fieldErrors.product_title} />
        </label>
      ) : null}
      {payload.case_type === "project_director_relation" ? (
        <>
          <label className="block text-sm font-medium text-ink/75">
            Estado de la relación
            <select value={payload.relationship_status} aria-invalid={Boolean(fieldErrors.relationship_status)} aria-describedby={fieldErrors.relationship_status ? `${idPrefix}-relationship-status-error` : undefined} onChange={(event) => onChange({ ...payload, relationship_status: event.target.value as LinkResolution })} className={inputClass}>
              {relationshipStatuses.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
            </select>
            <PayloadFieldError id={`${idPrefix}-relationship-status-error`} message={fieldErrors.relationship_status} />
          </label>
          <details className="rounded-[8px] border border-line p-3 text-sm text-ink/70">
            <summary className="cursor-pointer font-semibold">Ver identificador público avanzado</summary>
            <label className="mt-3 block font-medium text-ink/75">
              Referencia pública del director
              <input value={payload.project_director_identity_key ?? ""} aria-invalid={Boolean(fieldErrors.project_director_identity_key)} aria-describedby={fieldErrors.project_director_identity_key ? `${idPrefix}-director-reference-error` : undefined} onChange={(event) => onChange({ ...payload, project_director_identity_key: event.target.value || null })} maxLength={320} autoComplete="off" className={inputClass} />
              <PayloadFieldError id={`${idPrefix}-director-reference-error`} message={fieldErrors.project_director_identity_key} />
            </label>
          </details>
        </>
      ) : null}
      {payload.case_type === "external_identity" ? (
        <>
          <label className="block text-sm font-medium text-ink/75">
            Institución externa
            <input value={payload.external_institution ?? ""} aria-invalid={Boolean(fieldErrors.external_institution)} aria-describedby={fieldErrors.external_institution ? `${idPrefix}-external-institution-error` : undefined} onChange={(event) => onChange({ ...payload, external_institution: event.target.value || null })} maxLength={500} className={inputClass} />
            <PayloadFieldError id={`${idPrefix}-external-institution-error`} message={fieldErrors.external_institution} />
          </label>
          <div className="rounded-[8px] border border-line bg-paper/45 p-3 text-sm text-ink/65">
            <p className="font-semibold text-ink/75">Referencia pública externa</p>
            <p className="mt-1">La referencia pública externa se asignará automáticamente al confirmar.</p>
            <PayloadFieldError id={`${idPrefix}-external-reference-error`} message={fieldErrors.external_identity_key} />
          </div>
        </>
      ) : null}
      <div className="text-sm">
        <p className="font-medium text-ink/75">Estado científico</p>
        <p className="mt-1 rounded-[8px] border border-line bg-paper/70 px-3 py-2 font-semibold text-ink">Validado</p>
      </div>
    </fieldset>
  );
}

export function DecisionComposer({
  detail,
  capabilities,
  capabilitiesLoading,
  capabilitiesUpdating,
  capabilitiesError,
  onReloadCase,
  onSuccess,
  hideApplyActions = false
}: {
  detail: CaseDetail;
  capabilities?: Capabilities;
  capabilitiesLoading: boolean;
  capabilitiesUpdating: boolean;
  capabilitiesError: unknown;
  onReloadCase: () => Promise<CaseDetail>;
  onSuccess: (message: string | null) => void;
  hideApplyActions?: boolean;
}) {
  const commands = useHumanReviewCommands();
  const [draft, setDraft] = useState<DecisionDraft | null>(null);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [discardReason, setDiscardReason] = useState("");
  const [discardCorrelation, setDiscardCorrelation] = useState<string | null>(null);
  const [revertOpen, setRevertOpen] = useState(false);
  const [revertReason, setRevertReason] = useState("");
  const [revertCorrelation, setRevertCorrelation] = useState<string | null>(null);
  const [revertDecisionId, setRevertDecisionId] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<HumanReviewApiError | null>(null);
  const [conflictDialogOpen, setConflictDialogOpen] = useState(false);
  const [conflictSnapshot, setConflictSnapshot] = useState<ReturnType<typeof createOptimisticConflictSnapshot> | null>(null);
  const [isReloadingConflict, setIsReloadingConflict] = useState(false);
  const [reloadError, setReloadError] = useState<HumanReviewApiError | null>(null);

  useEffect(() => {
    setDraft(null);
    setDiscardOpen(false);
    setRevertOpen(false);
    setLocalError(null);
    setConflict(null);
    setConflictDialogOpen(false);
    setConflictSnapshot(null);
    setReloadError(null);
  }, [detail.id]);

  const editable = isEditableReviewStatus(detail.case_status);
  const capabilitiesBusy = capabilitiesLoading || capabilitiesUpdating;
  const confirmationBlocked = isOptimisticConfirmationBlocked(conflictSnapshot);
  const revertable = detail.case_status === "resolved" && Boolean(detail.current_decision_id);
  const actions = allowedDecisionActions(detail.case_type);
  const supportsDecisionCommands = actions.length > 0;
  const supportReference = commands.error?.correlation_id ? safePublicText(commands.error.correlation_id, "") : "";

  const clearConflictSnapshot = () => {
    setConflict(null);
    setConflictDialogOpen(false);
    setConflictSnapshot(null);
    setReloadError(null);
  };

  const abandonConflictOperation = () => {
    if (conflictSnapshot?.awaiting_reload) {
      setConflictSnapshot(abandonOptimisticConflictOperation(conflictSnapshot));
      setConflictDialogOpen(false);
      setReloadError(null);
      return;
    }
    clearConflictSnapshot();
  };

  const handleCommandError = (error: unknown, operation: "apply" | "discard" | "revert" = "apply", originalRevertDecisionId: string | null = null) => {
    if (error instanceof HumanReviewApiError && isOptimisticVersionConflict(error)) {
      setConflict(error);
      setConflictDialogOpen(true);
      setConflictSnapshot(createOptimisticConflictSnapshot(detail, draft, error, operation, originalRevertDecisionId));
      setReloadError(null);
      return;
    }
    setLocalError(safeCommandMessage(error));
  };

  const reloadAfterConflict = async () => {
    if (!conflict || !conflictSnapshot || isReloadingConflict) return;
    setIsReloadingConflict(true);
    setReloadError(null);
    try {
      const refreshed = await onReloadCase();
      const next = applyOptimisticConflictReload(conflictSnapshot, refreshed);
      setConflictSnapshot(next.operation_abandoned ? null : next);
      setConflict(null);
      setLocalError(next.needs_revision
        ? "El borrador ya no es compatible con el caso actualizado. Rev\u00edsalo antes de confirmar."
        : "Informaci\u00f3n actualizada. Revisa la vista previa y confirma manualmente si corresponde.");
    } catch (error) {
      const safeError = error instanceof HumanReviewApiError
        ? error
        : new HumanReviewApiError(new ApiFetchError("Request failed", { status: 0 }));
      setReloadError(safeError);
      setConflictSnapshot(keepOptimisticConflictAfterReloadFailure(conflictSnapshot, safeError));
    } finally {
      setIsReloadingConflict(false);
    }
  };

  const start = (action: DecisionAction) => {
    if (confirmationBlocked) {
      setLocalError("Resuelve el conflicto pendiente antes de iniciar otra acción.");
      return;
    }
    clearConflictSnapshot();
    onSuccess(null);
    setLocalError(null);
    try {
      const next = draft
        ? withDecisionAction(detail, draft, action)
        : createDecisionDraft(detail, action, correlationId());
      if (!next) {
        setLocalError(detail.case_type === "possible_duplicate"
          ? "No hay una contraparte pública disponible para preparar este vínculo."
          : "Esta acción no está disponible para el tipo de caso.");
        return;
      }
      setDraft(next);
    } catch (error) { handleCommandError(error, "apply"); }
  };

  const confirmApply = async () => {
    if (!draft || !editable || commands.isLoading || confirmationBlocked) return;
    setLocalError(null);
    onSuccess(null);
    try {
      const request = buildApplyDecisionRequest(detail, draft);
      const response = await commands.apply(detail.id, request);
      if (!response) return;
      setDraft(cancelDecisionDraft());
      onSuccess("La decisión fue aplicada correctamente.");
    } catch (error) { handleCommandError(error, "apply"); }
  };

  const openDiscard = () => {
    if (confirmationBlocked) {
      setLocalError("Resuelve el conflicto pendiente antes de iniciar otra acción.");
      return;
    }
    clearConflictSnapshot();
    try {
      setDiscardCorrelation(correlationId());
      setDiscardReason("");
      setLocalError(null);
      setDiscardOpen(true);
    } catch (error) { handleCommandError(error, "discard"); }
  };

  const confirmDiscard = async () => {
    if (!discardCorrelation || !editable || commands.isLoading || confirmationBlocked) return;
    setLocalError(null);
    try {
      const response = await commands.discard(detail.id, buildDiscardRequest(detail, discardReason, discardCorrelation));
      if (!response) return;
      setDiscardOpen(false);
      setDiscardReason("");
      setDiscardCorrelation(null);
      setDraft(cancelDecisionDraft());
      onSuccess("El caso fue descartado correctamente.");
    } catch (error) { handleCommandError(error, "discard"); }
  };

  const openRevert = () => {
    if (confirmationBlocked) {
      setLocalError("Resuelve el conflicto pendiente antes de iniciar otra acción.");
      return;
    }
    clearConflictSnapshot();
    try {
      onSuccess(null);
      setRevertCorrelation(correlationId());
      setRevertDecisionId(detail.current_decision_id);
      setRevertReason("");
      setLocalError(null);
      setRevertOpen(true);
    } catch (error) { handleCommandError(error); }
  };

  const confirmRevert = async () => {
    if (!revertCorrelation || !revertDecisionId || !revertable || commands.isLoading || confirmationBlocked) return;
    setLocalError(null);
    try {
      const response = await commands.revert(detail.id, buildRevertRequest(detail, revertReason, revertCorrelation, revertDecisionId));
      if (!response) return;
      setRevertOpen(false);
      setRevertReason("");
      setRevertCorrelation(null);
      setRevertDecisionId(null);
      setDraft(cancelDecisionDraft());
      onSuccess("La decisión fue revertida correctamente.");
    } catch (error) { handleCommandError(error, "revert", revertDecisionId); }
  };

  return (
    <section aria-labelledby="decision-composer-heading" className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-mint">Decisión</p>
          <h3 id="decision-composer-heading" className="mt-1 text-lg font-semibold text-ink">Preparar una decisión</h3>
          <p className="mt-1 max-w-2xl text-sm text-ink/55">El borrador y la vista previa existen solo en esta página. Nada se escribe hasta confirmar.</p>
        </div>
        {!editable ? <span className="rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/55">Solo lectura</span> : null}
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {!hideApplyActions ? actions.map((action) => {
          const Icon = actionIcons[action];
          const lacksPublicCounterpart = detail.case_type === "possible_duplicate" && action === "link" && detail.counterpart_options.length === 0;
          return <CapabilityGate key={action} action="apply_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><button type="button" disabled={!editable || confirmationBlocked || commands.isLoading || lacksPublicCounterpart} onClick={() => start(action)} className="focus-ring inline-flex items-center gap-2 rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink hover:bg-paper disabled:cursor-not-allowed disabled:opacity-45"><Icon size={16} aria-hidden="true" />{actionLabels[action]}</button></CapabilityGate>;
        }) : null}
        {supportsDecisionCommands ? <CapabilityGate action="apply_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><button type="button" disabled={!editable || confirmationBlocked || commands.isLoading} onClick={openDiscard} className="focus-ring inline-flex items-center gap-2 rounded-[8px] border border-coral/35 px-4 py-2 text-sm font-semibold text-coral hover:bg-coral/5 disabled:cursor-not-allowed disabled:opacity-45"><Trash2 size={16} aria-hidden="true" />Descartar</button></CapabilityGate> : null}
        <CapabilityGate action="revert_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><button type="button" disabled={!revertable || confirmationBlocked || commands.isLoading} onClick={openRevert} className="focus-ring inline-flex items-center gap-2 rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink hover:bg-paper disabled:cursor-not-allowed disabled:opacity-45"><RotateCcw size={16} aria-hidden="true" />Revertir</button></CapabilityGate>
      </div>

      {editable && !supportsDecisionCommands ? (
        <p className="mt-4 rounded-[8px] border border-line bg-paper/60 px-4 py-3 text-sm text-ink/65">Este tipo de caso no admite una decisión guiada desde esta pantalla.</p>
      ) : null}

      {!hideApplyActions && draft ? (
        <div className="mt-6 space-y-5 border-t border-line pt-5">
          <div className="flex items-center justify-between gap-3">
            <h4 className="font-semibold text-ink">Borrador local · {actionLabels[draft.action]}</h4>
            <button type="button" onClick={() => { setDraft(cancelDecisionDraft()); abandonConflictOperation(); setLocalError(null); }} disabled={commands.isLoading} className="focus-ring inline-flex items-center gap-1 rounded-[8px] px-3 py-1.5 text-sm font-semibold text-ink/60 hover:bg-paper"><X size={15} aria-hidden="true" />Cancelar</button>
          </div>
          <DecisionPayloadFields detail={detail} draft={draft} disabled={commands.isLoading} onChange={(payload) => {
            const nextDraft = withDecisionPayload(draft, payload);
            setDraft(nextDraft);
            if (conflictSnapshot?.operation === "apply" && !conflictSnapshot.awaiting_reload) {
              setConflictSnapshot(updateOptimisticConflictDraft(conflictSnapshot, detail, nextDraft));
            }
          }} />
          <label className="block text-sm font-medium text-ink/75">
            Motivo {draft.action === "approve" ? "cuando exista un cambio" : "obligatorio"}
            <textarea value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} maxLength={MAX_DECISION_REASON_LENGTH} rows={3} disabled={commands.isLoading} className={`${inputClass} resize-y`} />
          </label>
          <p className="-mt-4 text-right text-xs text-ink/45">{draft.reason.length}/{MAX_DECISION_REASON_LENGTH}</p>
          <DecisionPreview detail={detail} draft={draft} />
          <div className="flex justify-end">
            <CapabilityGate action="apply_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><button type="button" onClick={confirmApply} disabled={commands.isLoading || confirmationBlocked} className="focus-ring rounded-[8px] bg-ink px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{commands.isLoading ? "Confirmando..." : "Confirmar decisión"}</button></CapabilityGate>
          </div>
        </div>
      ) : null}

      {localError && !discardOpen && !revertOpen ? <div role="alert" className="mt-4 rounded-[8px] border border-coral/30 bg-coral/5 p-3 text-sm font-semibold text-coral">{localError}{supportReference ? <span className="mt-1 block font-normal text-ink/60">Referencia de soporte: {supportReference}</span> : null}</div> : null}
      {conflictSnapshot?.awaiting_reload && !conflictDialogOpen ? <div role="alert" className="mt-4 rounded-[8px] border border-coral/30 bg-coral/5 p-3 text-sm text-ink"><p className="font-semibold text-coral">Debes recargar el caso antes de confirmar nuevamente.</p><button type="button" onClick={() => setConflictDialogOpen(true)} className="focus-ring mt-2 rounded-[8px] border border-line px-3 py-1.5 text-sm font-semibold text-ink">Resolver conflicto</button></div> : null}
      <CapabilityGate action="apply_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><DiscardConfirmDialog open={discardOpen} reason={discardReason} pending={commands.isLoading} confirmationBlocked={confirmationBlocked} error={discardOpen ? localError : null} onReasonChange={setDiscardReason} onCancel={() => { if (!commands.isLoading) { setDiscardOpen(false); setDiscardReason(""); setDiscardCorrelation(null); abandonConflictOperation(); setLocalError(null); } }} onConfirm={confirmDiscard} /></CapabilityGate>
      <CapabilityGate action="revert_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={capabilitiesError}><RevertConfirmDialog open={revertOpen} reason={revertReason} pending={commands.isLoading} confirmationBlocked={confirmationBlocked} error={revertOpen ? localError : null} onReasonChange={setRevertReason} onCancel={() => { if (!commands.isLoading) { setRevertOpen(false); setRevertReason(""); setRevertCorrelation(null); setRevertDecisionId(null); abandonConflictOperation(); setLocalError(null); } }} onConfirm={confirmRevert} /></CapabilityGate>
      <OptimisticConflictDialog conflict={conflictDialogOpen ? conflict : null} isReloading={isReloadingConflict} reloadError={reloadError} onCancel={() => { if (!isReloadingConflict && conflictSnapshot) { setConflictSnapshot(keepOptimisticConflictAwaitingReload(conflictSnapshot)); setConflictDialogOpen(false); setReloadError(null); } }} onReload={reloadAfterConflict} />
    </section>
  );
}
