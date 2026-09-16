import { ApiFetchError, apiFetch, apiFetchBlob } from "./api";
import { publishEffectiveDataRefresh } from "./effective-data-refresh";
import type {
  ApplyDecisionRequest,
  ApplyDecisionResponse,
  AuditTimelineResponse,
  Capabilities,
  CaseDetail,
  DiscardRequest,
  HumanReviewErrorCode,
  HumanReviewErrorDetails,
  QueueQuery,
  QueueResponse,
  RelatedReviewResponse,
  RevertRequest
} from "./human-review";

export type CacheInvalidator = (prefix: string) => void;

export class HumanReviewApiError extends Error {
  readonly status: number;
  readonly code: HumanReviewErrorCode;
  readonly correlation_id: string;
  readonly details: HumanReviewErrorDetails | null;

  constructor(error: ApiFetchError) {
    super(error.message);
    this.name = "HumanReviewApiError";
    this.status = error.status;
    this.code = (error.code ?? "HUMAN_REVIEW_INTERNAL_ERROR") as HumanReviewErrorCode;
    this.correlation_id = error.correlation_id ?? "";
    this.details = (error.details as HumanReviewErrorDetails | null) ?? null;
  }
}

function buildQueueSearch(query: QueueQuery): string {
  const params = new URLSearchParams();
  params.set("page", String(query.page));
  params.set("page_size", String(query.page_size));
  for (const status of [...new Set(query.statuses)].sort()) params.append("status", status);
  for (const caseType of [...new Set(query.case_types)].sort()) params.append("case_type", caseType);
  if (query.period_id !== undefined) params.set("period_id", String(query.period_id));
  if (query.document_id !== undefined) params.set("document_id", String(query.document_id));
  if (query.source_revision !== undefined) params.set("source_revision", query.source_revision);
  if (query.created_from !== undefined) params.set("created_from", query.created_from);
  if (query.created_to !== undefined) params.set("created_to", query.created_to);
  if (query.q !== undefined) params.set("q", query.q);
  params.set("sort", query.sort);
  return params.toString();
}

async function humanReviewRequest<T>(path: string, options?: RequestInit): Promise<T> {
  try {
    return await apiFetch<T>(path, options);
  } catch (error) {
    if (error instanceof ApiFetchError) throw new HumanReviewApiError(error);
    throw new HumanReviewApiError(
      new ApiFetchError("Request failed", { status: 0 })
    );
  }
}

async function humanReviewBlobRequest(path: string, options?: RequestInit): Promise<Blob> {
  try {
    return await apiFetchBlob(path, options);
  } catch (error) {
    if (error instanceof ApiFetchError) throw new HumanReviewApiError(error);
    throw new HumanReviewApiError(new ApiFetchError("Request failed", { status: 0 }));
  }
}

function commandOptions(payload: ApplyDecisionRequest | DiscardRequest | RevertRequest): RequestInit {
  return {
    method: "POST",
    body: JSON.stringify(payload)
  };
}

function invalidateAfterCommand(invalidate: CacheInvalidator, caseId: string): void {
  invalidate(`human-review:case:${caseId}`);
  invalidate(`human-review:audit:${caseId}:`);
  invalidate("human-review:queue:");
  invalidate("dashboard:");
  invalidate("canonical-participants:");
  invalidate("human-review:related:");
  publishEffectiveDataRefresh();
}

export function humanReviewQueueKey(query: QueueQuery): string {
  return `human-review:queue:${buildQueueSearch(query)}`;
}

export function humanReviewCaseKey(caseId: string): string {
  return `human-review:case:${caseId}`;
}

export function humanReviewAuditKey(caseId: string, page: number, pageSize: number): string {
  return `human-review:audit:${caseId}:${page}:${pageSize}`;
}

export function humanReviewRelatedKey(caseId: string): string {
  return `human-review:related:${caseId}`;
}

export function canViewHumanReviewDetail(capabilities: Capabilities | undefined): boolean {
  return capabilities?.actions.includes("view_foundations") ?? false;
}

export const HUMAN_REVIEW_CAPABILITIES_KEY = "human-review:capabilities";

export function createHumanReviewApi(invalidate: CacheInvalidator = () => undefined) {
  return {
    getCapabilities(): Promise<Capabilities> {
      return humanReviewRequest<Capabilities>("/human-review/me");
    },

    listCases(query: QueueQuery): Promise<QueueResponse> {
      return humanReviewRequest<QueueResponse>(`/human-review/cases?${buildQueueSearch(query)}`);
    },

    getCase(caseId: string): Promise<CaseDetail> {
      return humanReviewRequest<CaseDetail>(`/human-review/cases/${encodeURIComponent(caseId)}`);
    },

    getRelated(caseId: string, limit = 50): Promise<RelatedReviewResponse> {
      return humanReviewRequest<RelatedReviewResponse>(
        `/human-review/cases/${encodeURIComponent(caseId)}/related?limit=${limit}`
      );
    },

    getAudit(caseId: string, page = 1, pageSize = 25): Promise<AuditTimelineResponse> {
      const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      return humanReviewRequest<AuditTimelineResponse>(
        `/human-review/cases/${encodeURIComponent(caseId)}/audit?${query}`
      );
    },

    getEvidence(caseId: string, signal?: AbortSignal): Promise<Blob> {
      return humanReviewBlobRequest(
        `/human-review/cases/${encodeURIComponent(caseId)}/evidence`,
        { signal }
      );
    },

    async apply(caseId: string, request: ApplyDecisionRequest): Promise<ApplyDecisionResponse> {
      const response = await humanReviewRequest<ApplyDecisionResponse>(
        `/human-review/cases/${encodeURIComponent(caseId)}/apply`,
        commandOptions(request)
      );
      invalidateAfterCommand(invalidate, caseId);
      return response;
    },

    async discard(caseId: string, request: DiscardRequest): Promise<ApplyDecisionResponse> {
      const response = await humanReviewRequest<ApplyDecisionResponse>(
        `/human-review/cases/${encodeURIComponent(caseId)}/discard`,
        commandOptions(request)
      );
      invalidateAfterCommand(invalidate, caseId);
      return response;
    },

    async revert(caseId: string, request: RevertRequest): Promise<ApplyDecisionResponse> {
      const response = await humanReviewRequest<ApplyDecisionResponse>(
        `/human-review/cases/${encodeURIComponent(caseId)}/revert`,
        commandOptions(request)
      );
      invalidateAfterCommand(invalidate, caseId);
      return response;
    }
  };
}

export const humanReviewApi = createHumanReviewApi();
