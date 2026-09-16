"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useCachedQuery, useDataCache } from "../lib/data-cache";
import { ApiFetchError } from "../lib/api";
import {
  buildApplyDecisionRequest,
  createOptimisticConflictSnapshot,
  isOptimisticVersionConflict,
  validateSessionDraft
} from "../lib/human-review";
import {
  createHumanReviewApi,
  HUMAN_REVIEW_CAPABILITIES_KEY,
  HumanReviewApiError,
  humanReviewApi,
  humanReviewAuditKey,
  humanReviewCaseKey,
  humanReviewQueueKey,
  humanReviewRelatedKey
} from "../lib/human-review-api";
import type {
  ApplyDecisionRequest,
  ApplyDecisionResponse,
  AuditTimelineResponse,
  Capabilities,
  CaseDetail,
  DiscardRequest,
  QueueQuery,
  QueueResponse,
  RelatedReviewResponse,
  ReviewCommandResult,
  ReviewSessionAction,
  ReviewSessionState,
  RevertRequest
} from "../lib/human-review";

type HumanReviewQueryState<T> = {
  data: T | undefined;
  error: HumanReviewApiError | null;
  isInitialLoading: boolean;
  isUpdating: boolean;
};

type QueryOptions = {
  enabled?: boolean;
  staleTimeMs?: number;
};

export type PendingCommandGate<T> = { current: Promise<T> | null };

export function runSinglePendingCommand<T>(
  gate: PendingCommandGate<T>,
  command: () => Promise<T>
): Promise<T | undefined> {
  if (gate.current) return Promise.resolve(undefined);
  const pending = command().finally(() => {
    if (gate.current === pending) gate.current = null;
  });
  gate.current = pending;
  return pending;
}

const commandResultMessages: Record<ReviewCommandResult["status"], string> = {
  confirmed: "Revisión confirmada.",
  conflict: "La revisión cambió. Recarga los datos antes de volver a confirmar.",
  forbidden: "No tienes permisos para confirmar esta revisión.",
  invalid: "La decisión contiene datos que deben corregirse.",
  unavailable: "El servicio no está disponible temporalmente. Intenta más tarde.",
  not_found: "La revisión ya no está disponible."
};

export function toReviewCommandResult(error: unknown): ReviewCommandResult {
  if (!(error instanceof HumanReviewApiError)) {
    return { status: "unavailable", correlationId: null, message: commandResultMessages.unavailable };
  }
  const status: ReviewCommandResult["status"] = error.status === 403
    ? "forbidden"
    : error.status === 404
      ? "not_found"
    : error.status === 409 && error.code === "REVIEW_CASE_VERSION_CONFLICT"
      ? "conflict"
      : error.status === 409 && error.code === "INCOMPATIBLE_DECISION"
        ? "invalid"
      : error.status === 422
          ? "invalid"
          : "unavailable";
  return {
    status,
    correlationId: error.correlation_id.trim() ? error.correlation_id.trim() : null,
    message: commandResultMessages[status]
  };
}

export type ConfirmValidDraftsOptions = {
  state: ReviewSessionState;
  getState?: () => ReviewSessionState;
  requestArbiter: ReviewRequestArbiter;
  detailByCaseId: Readonly<Record<string, CaseDetail | undefined>>;
  apply: (caseId: string, request: ApplyDecisionRequest) => Promise<ApplyDecisionResponse>;
  dispatch: (action: ReviewSessionAction) => void;
};

export type ReviewRequestArbiter = {
  isClaimed(caseId: string): boolean;
  tryClaim(caseId: string): number | null;
  release(caseId: string, token: number): void;
};

export function createReviewRequestArbiter(): ReviewRequestArbiter {
  let nextToken = 0;
  const claims = new Map<string, number>();
  return {
    isClaimed(caseId) {
      return claims.has(caseId);
    },
    tryClaim(caseId) {
      if (claims.has(caseId)) return null;
      nextToken += 1;
      claims.set(caseId, nextToken);
      return nextToken;
    },
    release(caseId, token) {
      if (claims.get(caseId) === token) claims.delete(caseId);
    }
  };
}

export async function confirmValidDrafts({
  state,
  getState = () => state,
  requestArbiter,
  detailByCaseId,
  apply,
  dispatch
}: ConfirmValidDraftsOptions): Promise<Record<string, ReviewCommandResult>> {
  const caseIds = Object.keys(state.draftByCaseId)
    .filter((caseId) => Boolean(state.draftByCaseId[caseId]))
    .sort((left, right) => left < right ? -1 : left > right ? 1 : 0);
  const prepared = caseIds.map((caseId) => {
    const detail = detailByCaseId[caseId];
    const draft = state.draftByCaseId[caseId];
    const errors = validateSessionDraft(detail, draft, state.conflictByCaseId[caseId]);
    return {
      caseId,
      detail,
      draft,
      errors,
      request: errors.length === 0 && detail && draft ? buildApplyDecisionRequest(detail, draft) : null
    };
  });

  for (const item of prepared) {
    if (requestArbiter.isClaimed(item.caseId)) continue;
    dispatch({ type: "validationChanged", caseId: item.caseId, errors: item.errors });
  }

  const results: Record<string, ReviewCommandResult> = {};
  for (const item of prepared) {
    if (item.request || requestArbiter.isClaimed(item.caseId)) continue;
    const result: ReviewCommandResult = {
      status: "invalid",
      correlationId: null,
      message: commandResultMessages.invalid
    };
    results[item.caseId] = result;
    dispatch({ type: "commandFailed", caseId: item.caseId, result });
  }

  const queued: (typeof prepared[number] & { request: ApplyDecisionRequest; requestToken: number })[] = [];
  for (const item of prepared) {
    if (!item.request) continue;
    const currentState = getState();
    const currentRequestState = currentState.requestStateByCaseId[item.caseId];
    if (currentRequestState === "queued" || currentRequestState === "sending") continue;
    const requestToken = requestArbiter.tryClaim(item.caseId);
    if (requestToken === null) continue;
    queued.push({ ...item, request: item.request, requestToken });
    dispatch({
      type: "requestStateChanged",
      caseId: item.caseId,
      state: "queued",
      requestToken,
      requestDraft: item.draft!
    });
  }

  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < queued.length) {
      const item = queued[nextIndex++];
      dispatch({
        type: "requestStateChanged",
        caseId: item.caseId,
        state: "sending",
        requestToken: item.requestToken,
        requestDraft: item.draft!
      });
      try {
        const response = await apply(item.caseId, item.request!);
        const result: ReviewCommandResult = {
          status: "confirmed",
          correlationId: response.correlation_id.trim() || null,
          message: commandResultMessages.confirmed
        };
        results[item.caseId] = result;
        dispatch({
          type: "commandSucceeded",
          caseId: item.caseId,
          result,
          requestToken: item.requestToken,
          requestDraft: item.draft!
        });
      } catch (error) {
        const result = toReviewCommandResult(error);
        results[item.caseId] = result;
        const conflict = item.detail && item.draft && isOptimisticVersionConflict(error)
          ? createOptimisticConflictSnapshot(item.detail, item.draft, error, "apply")
          : undefined;
        dispatch({
          type: "commandFailed",
          caseId: item.caseId,
          result,
          conflict,
          requestToken: item.requestToken,
          requestDraft: item.draft!
        });
      } finally {
        requestArbiter.release(item.caseId, item.requestToken);
      }
    }
  };

  await Promise.all(Array.from({ length: Math.min(2, queued.length) }, () => worker()));
  return results;
}

export function useHumanReviewQueue(
  query: QueueQuery,
  options: QueryOptions = {}
): HumanReviewQueryState<QueueResponse> {
  const key = humanReviewQueueKey(query);
  return useCachedQuery(key, () => humanReviewApi.listCases(query), options) as HumanReviewQueryState<QueueResponse>;
}

export function useHumanReviewCapabilities(
  options: QueryOptions = {}
): HumanReviewQueryState<Capabilities> {
  return useCachedQuery(
    HUMAN_REVIEW_CAPABILITIES_KEY,
    () => humanReviewApi.getCapabilities(),
    options
  ) as HumanReviewQueryState<Capabilities>;
}

export function useHumanReviewCase(
  caseId: string | null,
  options: QueryOptions = {}
): HumanReviewQueryState<CaseDetail> {
  const enabled = (options.enabled ?? true) && Boolean(caseId);
  const key = caseId ? humanReviewCaseKey(caseId) : "human-review:case:disabled";
  return useCachedQuery(
    key,
    () => humanReviewApi.getCase(caseId ?? ""),
    { ...options, enabled }
  ) as HumanReviewQueryState<CaseDetail>;
}

export function useHumanReviewRelated(
  caseId: string | null,
  options: QueryOptions = {}
): HumanReviewQueryState<RelatedReviewResponse> {
  const enabled = (options.enabled ?? true) && Boolean(caseId);
  const key = caseId ? humanReviewRelatedKey(caseId) : "human-review:related:disabled";
  return useCachedQuery(
    key,
    () => humanReviewApi.getRelated(caseId ?? ""),
    { ...options, enabled }
  ) as HumanReviewQueryState<RelatedReviewResponse>;
}

export function useHumanReviewAudit(
  caseId: string | null,
  page: number,
  pageSize: number,
  options: QueryOptions = {}
): HumanReviewQueryState<AuditTimelineResponse> {
  const enabled = (options.enabled ?? true) && Boolean(caseId);
  const key = caseId
    ? humanReviewAuditKey(caseId, page, pageSize)
    : "human-review:audit:disabled";
  return useCachedQuery(
    key,
    () => humanReviewApi.getAudit(caseId ?? "", page, pageSize),
    { ...options, enabled }
  ) as HumanReviewQueryState<AuditTimelineResponse>;
}

export function useActiveHumanReviewEvidence(caseId: string | null) {
  const [state, setState] = useState<{
    caseId: string | null;
    blob: Blob | null;
    error: HumanReviewApiError | null;
    isLoading: boolean;
  }>({ caseId: null, blob: null, error: null, isLoading: false });
  const requestEpoch = useRef(0);
  const activeController = useRef<AbortController | null>(null);

  const reload = useCallback(() => {
    const epoch = ++requestEpoch.current;
    activeController.current?.abort();
    activeController.current = null;
    setState({ caseId, blob: null, error: null, isLoading: Boolean(caseId) });

    if (!caseId) return;

    const controller = new AbortController();
    activeController.current = controller;
    void humanReviewApi.getEvidence(caseId, controller.signal)
      .then((nextBlob) => {
        if (requestEpoch.current !== epoch || controller.signal.aborted) return;
        setState({ caseId, blob: nextBlob, error: null, isLoading: true });
      })
      .catch((caught) => {
        if (requestEpoch.current !== epoch || controller.signal.aborted) return;
        setState({
          caseId,
          blob: null,
          error: caught instanceof HumanReviewApiError
            ? caught
            : new HumanReviewApiError(new ApiFetchError("Request failed", { status: 0 })),
          isLoading: true
        });
      })
      .finally(() => {
        if (requestEpoch.current === epoch && !controller.signal.aborted) {
          setState((current) => current.caseId === caseId
            ? { ...current, isLoading: false }
            : current);
        }
      });
  }, [caseId]);

  useEffect(() => {
    reload();
    return () => {
      requestEpoch.current += 1;
      activeController.current?.abort();
      activeController.current = null;
    };
  }, [reload]);

  const visibleState = state.caseId === caseId
    ? state
    : { blob: null, error: null, isLoading: Boolean(caseId) };
  return {
    blob: visibleState.blob,
    isLoading: visibleState.isLoading,
    error: visibleState.error,
    reload
  };
}

export function useHumanReviewCommands() {
  const cache = useDataCache();
  const client = useMemo(() => createHumanReviewApi(cache.invalidate), [cache.invalidate]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<HumanReviewApiError | null>(null);
  const pending = useRef<Promise<ApplyDecisionResponse> | null>(null);

  const run = useCallback((command: () => Promise<ApplyDecisionResponse>) => {
    if (pending.current) return Promise.resolve(undefined);
    setIsLoading(true);
    setError(null);
    return runSinglePendingCommand(pending, async () => {
      try {
        return await command();
      } catch (caught) {
        if (caught instanceof HumanReviewApiError) setError(caught);
        throw caught;
      } finally {
        setIsLoading(false);
      }
    });
  }, []);

  const apply = useCallback(
    (caseId: string, request: ApplyDecisionRequest) => run(() => client.apply(caseId, request)),
    [client, run]
  );
  const discard = useCallback(
    (caseId: string, request: DiscardRequest) => run(() => client.discard(caseId, request)),
    [client, run]
  );
  const revert = useCallback(
    (caseId: string, request: RevertRequest) => run(() => client.revert(caseId, request)),
    [client, run]
  );

  return { apply, discard, revert, isLoading, error };
}
