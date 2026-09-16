"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiFetchError } from "../../lib/api";
import {
  allowedDecisionActions,
  createDecisionDraft,
  createReviewSessionState,
  isEditableReviewStatus,
  isValidHumanReviewReload,
  reviewSessionReducer,
  validateSessionDraft,
  withDecisionAction,
  type Capabilities,
  type CaseDetail,
  type DecisionAction,
  type RelatedReviewItem,
  type ReviewSessionAction,
  type ReviewSessionState
} from "../../lib/human-review";
import { canViewHumanReviewDetail, HumanReviewApiError, humanReviewApi } from "../../lib/human-review-api";
import {
  confirmValidDrafts,
  createReviewRequestArbiter,
  useHumanReviewCapabilities,
  useHumanReviewCase,
  useHumanReviewRelated,
  useHumanReviewAudit
} from "../../hooks/useHumanReview";
import { AuditTimeline } from "./AuditTimeline";
import { CapabilityGate } from "./CapabilityGate";
import { DecisionComposer } from "./DecisionComposer";
import { DetectedDataPanel, DetailStateNotice } from "./DetectedDataPanel";
import { EvidenceWorkspace } from "./EvidenceWorkspace";
import { GuidedDecisionEditor } from "./GuidedDecisionEditor";
import { OptimisticConflictDialog } from "./OptimisticConflictDialog";
import { RelatedReviewList } from "./RelatedReviewList";
import { ReviewResultSummary } from "./ReviewResultSummary";
import { SessionConfirmationBar } from "./SessionConfirmationBar";

const actionLabels: Record<DecisionAction, string> = {
  approve: "Aprobar",
  correct: "Corregir",
  link: "Vincular"
};

function safeError(error: unknown): HumanReviewApiError {
  return error instanceof HumanReviewApiError
    ? error
    : new HumanReviewApiError(new ApiFetchError("Request failed", { status: 0 }));
}

function correlationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `review-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function relatedItemFromDetail(detail: CaseDetail): RelatedReviewItem {
  return {
    case_id: detail.id,
    case_type: detail.case_type,
    case_status: detail.case_status,
    scientific_status: detail.scientific_status,
    version: detail.version,
    current_decision_id: detail.current_decision_id,
    detected_value: detail.detected_value,
    normalized_value: detail.normalized_value,
    canonical_value: detail.canonical_value,
    possible_kpi_impact: detail.possible_kpi_impact,
    allowed_actions: allowedDecisionActions(detail.case_type),
    evidence_summary: detail.evidence_summary
  };
}

function withAnchor(items: RelatedReviewItem[], anchor: CaseDetail): RelatedReviewItem[] {
  return items.some((item) => item.case_id === anchor.id)
    ? items
    : [relatedItemFromDetail(anchor), ...items];
}

function sessionWithSelectedDrafts(state: ReviewSessionState, selectedCaseIds: readonly string[]): ReviewSessionState {
  const selected = new Set(selectedCaseIds);
  return {
    ...state,
    draftByCaseId: Object.fromEntries(
      Object.entries(state.draftByCaseId).filter(([caseId]) => selected.has(caseId))
    )
  };
}

export function ReviewSessionPage({ anchorCaseId }: { anchorCaseId: string | null }) {
  // Independent requests intentionally begin in the same render.
  const capabilities = useHumanReviewCapabilities();
  const anchor = useHumanReviewCase(anchorCaseId);
  const related = useHumanReviewRelated(anchorCaseId);
  const [liveMessage, setLiveMessage] = useState("");
  const capabilitiesBusy = capabilities.isInitialLoading || capabilities.isUpdating;
  const recognized = capabilities.data?.actions.includes("view_foundations") ?? false;
  const canView = !capabilities.isInitialLoading
    && !capabilities.error
    && recognized
    && canViewHumanReviewDetail(capabilities.data);

  if (!anchorCaseId) return <DetailStateNotice state="error" />;
  if (capabilities.isInitialLoading) return <DetailStateNotice state="loading" />;
  if (capabilities.error) return <DetailStateNotice state="error" error={capabilities.error} />;
  if (!canView) return <DetailStateNotice state="denied" />;
  if (anchor.isInitialLoading) return <DetailStateNotice state="loading" />;
  if (anchor.error && !anchor.data) return <DetailStateNotice state="error" error={anchor.error} />;
  if (!anchor.data) return <DetailStateNotice state="error" />;

  const relatedItems = related.data?.items?.length
    ? withAnchor(related.data.items, anchor.data)
    : [relatedItemFromDetail(anchor.data)];

  return <>
    {liveMessage ? <p role="status" aria-live="polite" aria-atomic="true" className="mb-6 rounded-[8px] border border-mint/40 bg-mint/10 p-4 text-sm font-semibold text-ink">{liveMessage}</p> : null}
    <ReviewSessionReady
      anchor={anchor.data}
      items={relatedItems}
      relatedError={related.error}
      relatedLoading={related.isInitialLoading}
      truncated={Boolean(related.data?.truncated)}
      capabilities={capabilities.data!}
      capabilitiesBusy={capabilitiesBusy}
      onLiveMessage={setLiveMessage}
    />
  </>;
}

function ReviewSessionReady({
  anchor,
  items,
  relatedError,
  relatedLoading,
  truncated,
  capabilities,
  capabilitiesBusy,
  onLiveMessage
}: {
  anchor: CaseDetail;
  items: RelatedReviewItem[];
  relatedError: unknown;
  relatedLoading: boolean;
  truncated: boolean;
  capabilities: Capabilities;
  capabilitiesBusy: boolean;
  onLiveMessage: (message: string) => void;
}) {
  const [state, rawDispatch] = useReducer(
    reviewSessionReducer,
    undefined,
    () => createReviewSessionState(items, anchor.id)
  );
  const stateRef = useRef(state);
  const activeCaseIdRef = useRef(state.activeCaseId);
  const [detailByCaseId, setDetailByCaseId] = useState<Record<string, CaseDetail | undefined>>({ [anchor.id]: anchor });
  const detailByCaseIdRef = useRef(detailByCaseId);
  const [detailErrorByCaseId, setDetailErrorByCaseId] = useState<Record<string, HumanReviewApiError | undefined>>({});
  const [detailLoadingCaseId, setDetailLoadingCaseId] = useState<string | null>(null);
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>(() =>
    items.filter((item) => isEditableReviewStatus(item.case_status)).map((item) => item.case_id)
  );
  const [previewByCaseId, setPreviewByCaseId] = useState<Record<string, boolean>>({});
  const [auditPage, setAuditPage] = useState(1);
  const [conflictDialogCaseId, setConflictDialogCaseId] = useState<string | null>(null);
  const [conflictReloading, setConflictReloading] = useState(false);
  const [conflictReloadError, setConflictReloadError] = useState<HumanReviewApiError | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const keyboardFocusPending = useRef(false);
  const selectionTouched = useRef(false);
  const inFlightDetails = useRef(new Map<string, Promise<CaseDetail>>());
  const requestArbiter = useRef(createReviewRequestArbiter());

  const dispatch = useCallback((action: ReviewSessionAction) => {
    stateRef.current = reviewSessionReducer(stateRef.current, action);
    rawDispatch(action);
  }, []);

  const rememberDetail = useCallback((detail: CaseDetail) => {
    detailByCaseIdRef.current = { ...detailByCaseIdRef.current, [detail.id]: detail };
    setDetailByCaseId(detailByCaseIdRef.current);
    setDetailErrorByCaseId((current) => ({ ...current, [detail.id]: undefined }));
  }, []);

  const loadDetail = useCallback(async (caseId: string, force = false) => {
    if (!force && detailByCaseIdRef.current[caseId]) return detailByCaseIdRef.current[caseId]!;
    if (!force && inFlightDetails.current.has(caseId)) return inFlightDetails.current.get(caseId)!;
    setDetailLoadingCaseId(caseId);
    setDetailErrorByCaseId((current) => ({ ...current, [caseId]: undefined }));
    const request = humanReviewApi.getCase(caseId)
      .then((detail) => {
        if (!isValidHumanReviewReload(caseId, detail)) throw new ApiFetchError("Request failed", { status: 0 });
        rememberDetail(detail);
        return detail;
      })
      .catch((error) => {
        const sanitized = safeError(error);
        setDetailErrorByCaseId((current) => ({ ...current, [caseId]: sanitized }));
        throw sanitized;
      })
      .finally(() => {
        inFlightDetails.current.delete(caseId);
        setDetailLoadingCaseId((current) => current === caseId ? null : current);
      });
    inFlightDetails.current.set(caseId, request);
    return request;
  }, [rememberDetail]);

  useEffect(() => {
    stateRef.current = state;
    activeCaseIdRef.current = state.activeCaseId;
  }, [state]);

  useEffect(() => rememberDetail(anchor), [anchor, rememberDetail]);

  useEffect(() => {
    dispatch({ type: "relatedRefreshed", items });
    if (!selectionTouched.current) {
      setSelectedCaseIds(items.filter((item) => isEditableReviewStatus(item.case_status)).map((item) => item.case_id));
    }
  }, [dispatch, items]);

  const activeCaseId = state.activeCaseId || anchor.id;
  const canViewAudit = capabilities.actions.includes("view_audit");
  const audit = useHumanReviewAudit(activeCaseId, auditPage, 25, { enabled: canViewAudit });
  const activeItem = items.find((item) => item.case_id === activeCaseId) ?? relatedItemFromDetail(anchor);
  const activeDetail = detailByCaseId[activeCaseId];
  const activeDetailError = detailErrorByCaseId[activeCaseId];
  const activeResult = state.resultByCaseId[activeCaseId];
  const activeConflict = state.conflictByCaseId[activeCaseId];
  const selectedConflict = conflictDialogCaseId ? state.conflictByCaseId[conflictDialogCaseId] : undefined;

  const selectCase = useCallback((caseId: string, interaction?: "pointer" | "keyboard") => {
    keyboardFocusPending.current = interaction === "keyboard";
    dispatch({ type: "select", caseId });
    activeCaseIdRef.current = caseId;
    void loadDetail(caseId).catch(() => undefined).finally(() => {
      if (keyboardFocusPending.current && activeCaseIdRef.current === caseId) {
        keyboardFocusPending.current = false;
        window.requestAnimationFrame(() => headingRef.current?.focus());
      }
    });
  }, [dispatch, loadDetail]);

  const startAction = useCallback((action: DecisionAction) => {
    if (!activeDetail) return;
    const current = stateRef.current.draftByCaseId[activeDetail.id];
    const next = current
      ? withDecisionAction(activeDetail, current, action)
      : createDecisionDraft(activeDetail, action, correlationId());
    if (!next) return;
    dispatch({ type: "draftChanged", caseId: activeDetail.id, draft: next });
    setPreviewByCaseId((current) => ({ ...current, [activeDetail.id]: true }));
    setSelectedCaseIds((selected) => selected.includes(activeDetail.id) ? selected : [...selected, activeDetail.id]);
  }, [activeDetail, dispatch]);

  const confirm = useCallback(async () => {
    const results = await confirmValidDrafts({
      state: sessionWithSelectedDrafts(stateRef.current, selectedCaseIds),
      getState: () => stateRef.current,
      requestArbiter: requestArbiter.current,
      detailByCaseId: detailByCaseIdRef.current,
      apply: async (caseId, request) => {
        const response = await humanReviewApi.apply(caseId, request);
        rememberDetail(response.case);
        return response;
      },
      dispatch
    });
    const values = Object.values(results);
    const confirmed = values.filter((result) => result.status === "confirmed").length;
    const failed = values.length - confirmed;
    onLiveMessage(confirmed === 1 && failed === 0
      ? "La decisión fue aplicada correctamente."
      : `${confirmed} revisiones confirmadas; ${failed} requieren atención.`);
    const conflictCaseId = Object.entries(results).find(([, result]) => result.status === "conflict")?.[0] ?? null;
    if (conflictCaseId) {
      setConflictReloadError(null);
      setConflictDialogCaseId(conflictCaseId);
    }
  }, [dispatch, onLiveMessage, rememberDetail, selectedCaseIds]);

  const reloadConflict = useCallback(async () => {
    if (!conflictDialogCaseId || conflictReloading) return;
    setConflictReloading(true);
    setConflictReloadError(null);
    try {
      const detail = await loadDetail(conflictDialogCaseId, true);
      dispatch({ type: "conflictReloaded", caseId: conflictDialogCaseId, detail });
      setConflictDialogCaseId(null);
      onLiveMessage("Información actualizada. Revisa el borrador antes de confirmar nuevamente.");
    } catch (error) {
      setConflictReloadError(safeError(error));
    } finally {
      setConflictReloading(false);
    }
  }, [conflictDialogCaseId, conflictReloading, dispatch, loadDetail, onLiveMessage]);

  const conflictForDialog = selectedConflict
    ? ({ status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: selectedConflict.correlation_id } as HumanReviewApiError)
    : null;
  const pendingActions = activeDetail
    ? allowedDecisionActions(activeDetail.case_type).filter((action) => activeItem.allowed_actions.includes(action))
    : [];
  const activeDraft = state.draftByCaseId[activeCaseId];
  const activeValidationMessages = activeDraft && activeDetail
    ? validateSessionDraft(activeDetail, activeDraft, activeConflict)
    : state.validationByCaseId[activeCaseId] ?? [];
  const sessionBusy = Object.values(state.requestStateByCaseId).some((value) => value === "queued" || value === "sending");
  const hasClearableArtifacts = Object.values(state.draftByCaseId).some(Boolean)
    || Object.values(state.validationByCaseId).some((errors) => errors.length > 0)
    || Object.values(state.resultByCaseId).some(Boolean)
    || Object.values(state.conflictByCaseId).some(Boolean)
    || Object.values(state.requestStateByCaseId).some((requestState) => requestState !== "idle");

  return (
    <section aria-labelledby="review-session-heading" className="space-y-6">
      <div className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-mint">Validación humana</p>
        <h3 id="review-session-heading" className="mt-1 text-2xl font-semibold text-ink">Sesión de validación</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-ink/60">Prepara varias revisiones en memoria y confirma cada caso de forma independiente. Los borradores se descartan al salir o recargar.</p>
        <p className="mt-2 text-xs text-ink/50">Auditoría disponible en el registro individual de cada caso.</p>
      </div>

      {relatedError ? <div role="alert" className="rounded-[8px] border border-amber-300 bg-amber-50 p-3 text-sm text-ink">No pudimos cargar todas las revisiones relacionadas. Puedes continuar con el caso actual.</div> : null}
      {relatedLoading ? <p role="status" className="text-sm text-ink/55">Buscando revisiones relacionadas…</p> : null}
      {truncated ? <p role="status" className="rounded-[8px] border border-line bg-paper p-3 text-sm text-ink/65">Se muestran las primeras revisiones relacionadas disponibles. Refina la sesión desde la bandeja para consultar las restantes.</p> : null}

      <RelatedReviewList
        items={items}
        activeCaseId={activeCaseId}
        selectedCaseIds={selectedCaseIds}
        resultByCaseId={state.resultByCaseId}
        requestStateByCaseId={state.requestStateByCaseId}
        onSelect={selectCase}
        onToggleSelected={(caseId, selected) => {
          selectionTouched.current = true;
          setSelectedCaseIds((current) => selected
            ? current.includes(caseId) ? current : [...current, caseId]
            : current.filter((value) => value !== caseId));
        }}
      />

      <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.35fr)] md:items-start">
        <div className="min-w-0 [@media(min-width:768px)_and_(min-height:760px)]:sticky [@media(min-width:768px)_and_(min-height:760px)]:top-6">
          <EvidenceWorkspace caseId={activeCaseId} documentId={activeDetail?.document_id} evidenceSummary={activeItem.evidence_summary} responsiveSession />
        </div>

        <section aria-label="Validación activa" className="min-w-0 space-y-5">
          <h3 ref={headingRef} tabIndex={-1} className="focus-ring rounded-[8px] text-xl font-semibold text-ink motion-reduce:scroll-auto">Revisión activa</h3>
          {detailLoadingCaseId === activeCaseId && !activeDetail ? <DetailStateNotice state="loading" /> : null}
          {activeDetailError && !activeDetail ? (
            <div className="space-y-3">
              <DetailStateNotice state="error" error={activeDetailError} />
              <button type="button" onClick={() => void loadDetail(activeCaseId, true).catch(() => undefined)} className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink">Reintentar esta revisión</button>
            </div>
          ) : null}

          {activeDetail ? (
            <>
              <DetectedDataPanel detail={activeDetail} />
              {canViewAudit ? (
                <AuditTimeline response={audit.data} error={audit.error} isInitialLoading={audit.isInitialLoading} onPageChange={setAuditPage} />
              ) : null}
              {activeResult?.status === "confirmed" ? <ReviewResultSummary result={activeResult} /> : null}
              {isEditableReviewStatus(activeDetail.case_status) && activeResult?.status !== "confirmed" ? (
                <div className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
                  <div className="flex flex-wrap gap-2">
                    {pendingActions.map((action) => (
                      <CapabilityGate key={action} action="apply_scientific" capabilities={capabilities} isLoading={capabilitiesBusy} error={null}>
                        <button type="button" disabled={sessionBusy || Boolean(activeConflict?.awaiting_reload)} onClick={() => startAction(action)} className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-50">{actionLabels[action]}</button>
                      </CapabilityGate>
                    ))}
                  </div>
                  {activeDraft ? (
                    <div className="mt-5 space-y-5 border-t border-line pt-5">
                      <div className="flex items-center justify-between gap-3">
                        <p className="font-semibold text-ink">Borrador local · {actionLabels[activeDraft.action]}</p>
                        <button type="button" disabled={sessionBusy} onClick={() => dispatch({ type: "cancelDraft", caseId: activeCaseId })} className="focus-ring rounded-[8px] px-3 py-1.5 text-sm font-semibold text-ink/65 hover:bg-paper disabled:opacity-50">Cancelar</button>
                      </div>
                      <GuidedDecisionEditor
                        detail={activeDetail}
                        draft={activeDraft}
                        disabled={sessionBusy || Boolean(activeConflict?.awaiting_reload)}
                        previewOpen={Boolean(previewByCaseId[activeCaseId])}
                        validationMessages={activeValidationMessages}
                        onChange={(draft) => dispatch({ type: "draftChanged", caseId: activeCaseId, draft })}
                        onPreviewOpenChange={(open) => setPreviewByCaseId((current) => ({ ...current, [activeCaseId]: open }))}
                      />
                    </div>
                  ) : null}
                  {activeConflict?.awaiting_reload ? (
                    <div role="alert" className="mt-4 rounded-[8px] border border-coral/30 bg-coral/5 p-3 text-sm text-ink">
                      <p className="font-semibold text-coral">Esta revisión cambió y requiere una recarga explícita.</p>
                      <button type="button" onClick={() => setConflictDialogCaseId(activeCaseId)} className="focus-ring mt-2 rounded-[8px] border border-line px-3 py-1.5 font-semibold">Resolver conflicto</button>
                    </div>
                  ) : null}
                </div>
              ) : null}

              <DecisionComposer
                detail={activeDetail}
                capabilities={capabilities}
                capabilitiesLoading={false}
                capabilitiesUpdating={capabilitiesBusy}
                capabilitiesError={null}
                hideApplyActions
                onReloadCase={() => loadDetail(activeDetail.id, true)}
                onSuccess={(message) => {
                  if (!message) return;
                  onLiveMessage(message);
                  void loadDetail(activeDetail.id, true).catch(() => undefined);
                }}
              />
            </>
          ) : null}
        </section>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <button
          type="button"
          disabled={sessionBusy || !hasClearableArtifacts}
          onClick={() => {
            if (window.confirm("Se eliminarán todos los borradores y resultados locales de esta sesión. ¿Continuar?")) {
              dispatch({ type: "clearAll" });
              onLiveMessage("Se limpiaron los borradores y resultados locales.");
            }
          }}
          className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-50"
        >Limpiar borradores</button>
        <div className="min-w-0 flex-1 md:max-w-2xl">
          <SessionConfirmationBar
            selectedCaseIds={selectedCaseIds}
            draftByCaseId={state.draftByCaseId}
            detailByCaseId={detailByCaseId}
            validationByCaseId={state.validationByCaseId}
            conflictByCaseId={state.conflictByCaseId}
            resultByCaseId={state.resultByCaseId}
            requestStateByCaseId={state.requestStateByCaseId}
            onConfirm={() => void confirm()}
          />
        </div>
      </div>

      <OptimisticConflictDialog
        conflict={conflictForDialog}
        isReloading={conflictReloading}
        reloadError={conflictReloadError}
        onCancel={() => {
          if (!conflictReloading) {
            setConflictDialogCaseId(null);
            setConflictReloadError(null);
          }
        }}
        onReload={() => void reloadConflict()}
      />
    </section>
  );
}
