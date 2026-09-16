export type ReviewCaseStatus =
  | "pending"
  | "in_review"
  | "awaiting_gestor_approval"
  | "resolved"
  | "reopened"
  | "conflicted"
  | "superseded";

const reviewCaseStatuses: readonly ReviewCaseStatus[] = ["pending", "in_review", "awaiting_gestor_approval", "resolved", "reopened", "conflicted", "superseded"];

export type ReviewCaseType =
  | "person_identity"
  | "author_identity"
  | "product"
  | "project_director_relation"
  | "external_identity"
  | "possible_duplicate"
  | "invalid_text"
  | "new_evidence_conflict";

export type ScientificStatus = "pending" | "validated" | "rejected" | "discarded";

export type ReviewTargetTable =
  | "person_roles"
  | "scientific_production_authors"
  | "scientific_productions"
  | "research_entities"
  | "external_researchers";

export type OverrideField =
  | "canonical_identity_key"
  | "canonical_name"
  | "product_title"
  | "author_identity_key"
  | "project_director_identity_key"
  | "project_director_relationship_status"
  | "external_identity_key"
  | "external_institution"
  | "scientific_status";

export type DecisionScope = "global_identity" | "record" | "document" | "relationship" | "period";
export type DecisionAction = "approve" | "correct" | "link";
export type LinkResolution = "linked" | "maintained_separate" | "separated";
export type DuplicateResolution = "merged" | "maintained_separate" | "separated";

export type B2BCapability = "RESEARCH_MANAGER" | "SYSTEM_ADMIN";
export type B2BAction =
  | "view_foundations"
  | "view_audit"
  | "apply_scientific"
  | "propose_scientific"
  | "revert_scientific"
  | "manage_technical_access";

export type QueueQuery = {
  page: number;
  page_size: number;
  statuses: ReviewCaseStatus[];
  case_types: ReviewCaseType[];
  period_id?: number;
  document_id?: number;
  source_revision?: string;
  created_from?: string;
  created_to?: string;
  q?: string;
  sort: "priority_oldest";
};

export type QueueItem = {
  id: string;
  case_type: ReviewCaseType;
  case_status: ReviewCaseStatus;
  scientific_status: ScientificStatus;
  document_id?: number | null;
  document_name?: string | null;
  source_revision: string | null;
  source_page: number | null;
  source_section: string;
  detected_value?: string | null;
  normalized_value?: string | null;
  canonical_value?: string | null;
  automatic_priority: number;
  manual_priority: number | null;
  possible_kpi_impact: boolean;
  version: number;
  created_at: string;
};

export type QueueFacets = {
  statuses?: Partial<Record<ReviewCaseStatus, number>>;
  case_types?: Partial<Record<ReviewCaseType, number>>;
};

export type QueueResponse = {
  items: QueueItem[];
  total: number;
  page: number;
  page_size: number;
  facets: QueueFacets;
  correlation_id: string;
};

export type PublicScalar = string | number | boolean | null;

export type ReviewOverride = {
  decision_id?: PublicScalar;
  field?: PublicScalar;
  field_path?: PublicScalar;
  scope?: PublicScalar;
  scope_id?: PublicScalar;
  value?: PublicScalar;
  created_at?: PublicScalar;
};

export type EvidenceSummary = {
  available?: PublicScalar;
  count?: PublicScalar;
  document_name?: PublicScalar;
  page?: PublicScalar;
  section?: PublicScalar;
  locator?: PublicScalar;
  fragment?: PublicScalar;
  stream_path?: PublicScalar;
};

export type RelatedReviewEntity = {
  public_type: "person" | "scientific_product" | "research_entity" | "external_researcher";
  public_id: string;
  display_name: string;
};

export type RelatedReviewItem = {
  case_id: string;
  case_type: ReviewCaseType;
  case_status: ReviewCaseStatus;
  scientific_status: ScientificStatus;
  version: number;
  current_decision_id: string | null;
  detected_value: string | null;
  normalized_value: string | null;
  canonical_value: string | null;
  possible_kpi_impact: boolean;
  allowed_actions: DecisionAction[];
  evidence_summary: EvidenceSummary;
};

export type RelatedReviewResponse = {
  entity: RelatedReviewEntity;
  items: RelatedReviewItem[];
  total_pending: number;
  truncated: boolean;
  correlation_id: string;
};

export type CounterpartReference = {
  target_type: ReviewTargetTable;
  target_id: number;
};

export type CounterpartOption = {
  counterpart_ref: CounterpartReference;
  display_name: string;
  source_label?: string | null;
  document_name?: string | null;
};

export type CaseDetail = QueueItem & {
  target_table: ReviewTargetTable;
  target_pk: number | null;
  field_path: OverrideField | "case";
  detected_value: string | null;
  normalized_value: string | null;
  canonical_value: string | null;
  current_decision_id: string | null;
  overrides: ReviewOverride[];
  effective_memberships: string[];
  counterpart_options: CounterpartOption[];
  evidence_summary: EvidenceSummary;
};

export type Capabilities = {
  capability: B2BCapability | null;
  actions: B2BAction[];
};

export type AuditKpiEffect = {
  metric?: PublicScalar;
  before?: PublicScalar;
  after?: PublicScalar;
  delta?: PublicScalar;
};

export type AuditPayload = {
  kind?: PublicScalar;
  schema_version?: PublicScalar;
  decision_id?: PublicScalar;
  decision_type?: PublicScalar;
  previous_case_status?: PublicScalar;
  resulting_case_status?: PublicScalar;
  review_item_id?: PublicScalar;
  kpi_effect?: AuditKpiEffect[];
};

export type AuditItem = {
  id?: PublicScalar;
  event_type?: PublicScalar;
  actor_id?: PublicScalar;
  review_item_id?: PublicScalar;
  decision_id?: PublicScalar;
  created_at?: PublicScalar;
  correlation_id?: PublicScalar;
  summary?: PublicScalar;
  payload?: AuditPayload;
};

export type AuditTimelineResponse = {
  items: AuditItem[];
  page: number;
  page_size: number;
  total: number;
  correlation_id: string;
};

export type PersonPayload = {
  case_type: "person_identity" | "author_identity";
  canonical_identity_key: string | null;
  canonical_name: string;
  aliases?: string[];
  scientific_status: ScientificStatus;
  resolution?: LinkResolution | null;
};

export type ProductPayload = {
  case_type: "product";
  product_title?: string | null;
  scientific_status: ScientificStatus;
};

export type RelationPayload = {
  case_type: "project_director_relation";
  project_director_identity_key?: string | null;
  relationship_status: LinkResolution;
  scientific_status: ScientificStatus;
};

export type ExternalPayload = {
  case_type: "external_identity";
  external_identity_key: string | null;
  external_institution?: string | null;
  scientific_status: ScientificStatus;
  resolution?: LinkResolution | null;
};

export type DuplicatePayload = {
  case_type: "possible_duplicate";
  counterpart_ref: CounterpartReference;
  resolution: DuplicateResolution;
  scientific_status: ScientificStatus;
};

export type DecisionPayload =
  | PersonPayload
  | ProductPayload
  | RelationPayload
  | ExternalPayload
  | DuplicatePayload;

export type ApplyDecisionRequest = {
  expected_version: number;
  expected_current_decision_id: string | null;
  action: DecisionAction;
  scope: DecisionScope;
  payload: DecisionPayload;
  reason?: string | null;
  correlation_id: string;
};

export type KpiEffect = {
  affected: {
    metric: string;
    before: number;
    after: number;
    delta: number;
  }[];
};

export type ApplyDecisionResponse = {
  case: CaseDetail;
  decision_id: string;
  kpi_effect: KpiEffect;
  correlation_id: string;
};

export type DiscardRequest = {
  expected_version: number;
  expected_current_decision_id: string | null;
  reason: string;
  correlation_id: string;
};

export type RevertRequest = {
  expected_version: number;
  expected_current_decision_id: string;
  decision_id_to_revert: string;
  reason: string;
  correlation_id: string;
};

export const MAX_DECISION_REASON_LENGTH = 4000;

export type DecisionDraft = {
  action: DecisionAction;
  scope: DecisionScope;
  payload: DecisionPayload;
  reason: string;
  correlation_id: string;
};

export type DecisionPreviewModel = {
  action: DecisionAction;
  scope: DecisionScope;
  currentValue: string | null;
  proposedValue: string | null;
  changedFields: string[];
  selectedLink: string | null;
  proposedFields: { label: string; value: string | null }[];
  possibleKpiImpact: boolean;
};

export type OptimisticConflictError = {
  status: number;
  code: string;
  correlation_id: string;
};

export type OptimisticConflictOperation = "apply" | "discard" | "revert";

export type OptimisticConflictSnapshot = {
  operation: OptimisticConflictOperation;
  revert_decision_id: string | null;
  draft: DecisionDraft | null;
  preview: DecisionPreviewModel | null;
  cas: { expected_version: number; expected_current_decision_id: string | null };
  correlation_id: string;
  reload_error_correlation_id: string | null;
  requires_manual_confirmation: boolean;
  command_retried: false;
  needs_revision: boolean;
  awaiting_reload: boolean;
  operation_abandoned: boolean;
};

export type ReviewCommandResult = {
  status: "confirmed" | "conflict" | "forbidden" | "invalid" | "unavailable" | "not_found";
  correlationId: string | null;
  message: string;
};

export type ReviewRequestState = "idle" | "queued" | "sending" | "succeeded" | "failed";

export type ReviewSessionState = {
  activeCaseId: string;
  draftByCaseId: Record<string, DecisionDraft | undefined>;
  validationByCaseId: Record<string, string[]>;
  resultByCaseId: Record<string, ReviewCommandResult | undefined>;
  conflictByCaseId: Record<string, OptimisticConflictSnapshot | undefined>;
  requestStateByCaseId: Record<string, ReviewRequestState>;
  requestTokenByCaseId: Record<string, number | null>;
  requestDraftByCaseId: Record<string, DecisionDraft | undefined>;
};

export type ReviewSessionAction =
  | { type: "select"; caseId: string }
  | { type: "draftChanged"; caseId: string; draft: DecisionDraft }
  | { type: "cancelDraft"; caseId: string }
  | { type: "validationChanged"; caseId: string; errors: string[] }
  | { type: "requestStateChanged"; caseId: string; state: ReviewRequestState; requestToken?: number; requestDraft?: DecisionDraft }
  | { type: "commandSucceeded"; caseId: string; result: ReviewCommandResult; requestToken?: number; requestDraft?: DecisionDraft }
  | { type: "commandFailed"; caseId: string; result: ReviewCommandResult; conflict?: OptimisticConflictSnapshot; requestToken?: number; requestDraft?: DecisionDraft }
  | { type: "conflictReloaded"; caseId: string; detail: CaseDetail }
  | { type: "relatedRefreshed"; items: RelatedReviewItem[] }
  | { type: "clearAll" };

function keyedSessionMap<T>(items: readonly RelatedReviewItem[], value: () => T): Record<string, T> {
  return Object.fromEntries(items.map((item) => [item.case_id, value()]));
}

export function createReviewSessionState(
  items: readonly RelatedReviewItem[],
  requestedActiveCaseId = ""
): ReviewSessionState {
  const activeCaseId = items.some((item) => item.case_id === requestedActiveCaseId)
    ? requestedActiveCaseId
    : items[0]?.case_id ?? "";
  return {
    activeCaseId,
    draftByCaseId: keyedSessionMap(items, () => undefined),
    validationByCaseId: keyedSessionMap(items, () => []),
    resultByCaseId: keyedSessionMap(items, () => undefined),
    conflictByCaseId: keyedSessionMap(items, () => undefined),
    requestStateByCaseId: keyedSessionMap(items, () => "idle" as const),
    requestTokenByCaseId: keyedSessionMap(items, () => null),
    requestDraftByCaseId: keyedSessionMap(items, () => undefined)
  };
}

function withCaseValue<T>(values: Record<string, T>, caseId: string, value: T): Record<string, T> {
  if (Object.is(values[caseId], value) && Object.hasOwn(values, caseId)) return values;
  return { ...values, [caseId]: value };
}

function clearSessionMap<T>(values: Record<string, T>, value: () => T): Record<string, T> {
  return Object.fromEntries(Object.keys(values).map((caseId) => [caseId, value()]));
}

export function isEditableReviewStatus(status: ReviewCaseStatus): boolean {
  return status === "pending" || status === "reopened";
}

function relatedDraftIsCompatible(item: RelatedReviewItem, draft: DecisionDraft): boolean {
  return isEditableReviewStatus(item.case_status)
    && item.case_type === draft.payload.case_type
    && item.allowed_actions.includes(draft.action);
}

function terminalActionOwnsCase(
  state: ReviewSessionState,
  action: { caseId: string; requestToken?: number; requestDraft?: DecisionDraft }
): boolean {
  if (action.requestToken === undefined || action.requestDraft === undefined) {
    return state.requestTokenByCaseId[action.caseId] === null;
  }
  return state.requestTokenByCaseId[action.caseId] === action.requestToken
    && state.requestDraftByCaseId[action.caseId] === action.requestDraft
    && state.draftByCaseId[action.caseId] === action.requestDraft;
}

export function reviewSessionReducer(state: ReviewSessionState, action: ReviewSessionAction): ReviewSessionState {
  switch (action.type) {
    case "select":
      return action.caseId === state.activeCaseId ? state : { ...state, activeCaseId: action.caseId };
    case "draftChanged":
      return {
        ...state,
        draftByCaseId: withCaseValue(state.draftByCaseId, action.caseId, action.draft),
        validationByCaseId: withCaseValue(state.validationByCaseId, action.caseId, []),
        resultByCaseId: withCaseValue(state.resultByCaseId, action.caseId, undefined),
        requestStateByCaseId: withCaseValue(state.requestStateByCaseId, action.caseId, "idle"),
        requestTokenByCaseId: withCaseValue(state.requestTokenByCaseId, action.caseId, null),
        requestDraftByCaseId: withCaseValue(state.requestDraftByCaseId, action.caseId, undefined)
      };
    case "cancelDraft":
      return {
        ...state,
        draftByCaseId: withCaseValue(state.draftByCaseId, action.caseId, undefined),
        validationByCaseId: withCaseValue(state.validationByCaseId, action.caseId, []),
        requestStateByCaseId: withCaseValue(state.requestStateByCaseId, action.caseId, "idle"),
        requestTokenByCaseId: withCaseValue(state.requestTokenByCaseId, action.caseId, null),
        requestDraftByCaseId: withCaseValue(state.requestDraftByCaseId, action.caseId, undefined)
      };
    case "validationChanged":
      return {
        ...state,
        validationByCaseId: withCaseValue(state.validationByCaseId, action.caseId, [...action.errors])
      };
    case "requestStateChanged": {
      const establishesOwnership = (action.state === "queued" || action.state === "sending")
        && action.requestToken !== undefined
        && action.requestDraft !== undefined;
      if (establishesOwnership) {
        const currentToken = state.requestTokenByCaseId[action.caseId];
        if (currentToken !== null && currentToken !== action.requestToken) return state;
        if (state.draftByCaseId[action.caseId] !== action.requestDraft) return state;
      }
      return {
        ...state,
        requestStateByCaseId: withCaseValue(state.requestStateByCaseId, action.caseId, action.state),
        requestTokenByCaseId: establishesOwnership
          ? withCaseValue(state.requestTokenByCaseId, action.caseId, action.requestToken!)
          : state.requestTokenByCaseId,
        requestDraftByCaseId: establishesOwnership
          ? withCaseValue(state.requestDraftByCaseId, action.caseId, action.requestDraft)
          : state.requestDraftByCaseId
      };
    }
    case "commandSucceeded":
      if (!terminalActionOwnsCase(state, action)) return state;
      return {
        ...state,
        draftByCaseId: withCaseValue(state.draftByCaseId, action.caseId, undefined),
        validationByCaseId: withCaseValue(state.validationByCaseId, action.caseId, []),
        resultByCaseId: withCaseValue(state.resultByCaseId, action.caseId, action.result),
        conflictByCaseId: withCaseValue(state.conflictByCaseId, action.caseId, undefined),
        requestStateByCaseId: withCaseValue(state.requestStateByCaseId, action.caseId, "succeeded"),
        requestTokenByCaseId: withCaseValue(state.requestTokenByCaseId, action.caseId, null),
        requestDraftByCaseId: withCaseValue(state.requestDraftByCaseId, action.caseId, undefined)
      };
    case "commandFailed":
      if (!terminalActionOwnsCase(state, action)) return state;
      return {
        ...state,
        resultByCaseId: withCaseValue(state.resultByCaseId, action.caseId, action.result),
        conflictByCaseId: action.conflict
          ? withCaseValue(state.conflictByCaseId, action.caseId, action.conflict)
          : state.conflictByCaseId,
        requestStateByCaseId: withCaseValue(state.requestStateByCaseId, action.caseId, "failed"),
        requestTokenByCaseId: withCaseValue(state.requestTokenByCaseId, action.caseId, null),
        requestDraftByCaseId: withCaseValue(state.requestDraftByCaseId, action.caseId, undefined)
      };
    case "conflictReloaded": {
      const conflict = state.conflictByCaseId[action.caseId];
      if (!conflict) return state;
      const refreshed = applyOptimisticConflictReload(conflict, action.detail);
      return {
        ...state,
        conflictByCaseId: withCaseValue(state.conflictByCaseId, action.caseId, refreshed),
        validationByCaseId: withCaseValue(
          state.validationByCaseId,
          action.caseId,
          refreshed.needs_revision ? ["La revisión cambió y el borrador debe revisarse antes de confirmar."] : []
        )
      };
    }
    case "relatedRefreshed": {
      let draftByCaseId = state.draftByCaseId;
      let validationByCaseId = state.validationByCaseId;
      let resultByCaseId = state.resultByCaseId;
      let conflictByCaseId = state.conflictByCaseId;
      let requestStateByCaseId = state.requestStateByCaseId;
      let requestTokenByCaseId = state.requestTokenByCaseId;
      let requestDraftByCaseId = state.requestDraftByCaseId;
      for (const item of action.items) {
        if (!Object.hasOwn(draftByCaseId, item.case_id)) {
          draftByCaseId = withCaseValue(draftByCaseId, item.case_id, undefined);
          validationByCaseId = withCaseValue(validationByCaseId, item.case_id, []);
          resultByCaseId = withCaseValue(resultByCaseId, item.case_id, undefined);
          conflictByCaseId = withCaseValue(conflictByCaseId, item.case_id, undefined);
          requestStateByCaseId = withCaseValue(requestStateByCaseId, item.case_id, "idle");
          requestTokenByCaseId = withCaseValue(requestTokenByCaseId, item.case_id, null);
          requestDraftByCaseId = withCaseValue(requestDraftByCaseId, item.case_id, undefined);
        }
        const draft = draftByCaseId[item.case_id];
        if (!draft) continue;
        const errors = relatedDraftIsCompatible(item, draft)
          ? validationByCaseId[item.case_id] ?? []
          : ["El borrador ya no es compatible con la revisión actual y debe revisarse."];
        if (errors !== validationByCaseId[item.case_id]) {
          validationByCaseId = withCaseValue(validationByCaseId, item.case_id, errors);
        }
      }
      if (draftByCaseId === state.draftByCaseId && validationByCaseId === state.validationByCaseId) return state;
      return {
        ...state,
        draftByCaseId,
        validationByCaseId,
        resultByCaseId,
        conflictByCaseId,
        requestStateByCaseId,
        requestTokenByCaseId,
        requestDraftByCaseId
      };
    }
    case "clearAll":
      return {
        ...state,
        draftByCaseId: clearSessionMap(state.draftByCaseId, () => undefined),
        validationByCaseId: clearSessionMap(state.validationByCaseId, () => []),
        resultByCaseId: clearSessionMap(state.resultByCaseId, () => undefined),
        conflictByCaseId: clearSessionMap(state.conflictByCaseId, () => undefined),
        requestStateByCaseId: clearSessionMap(state.requestStateByCaseId, () => "idle" as const),
        requestTokenByCaseId: clearSessionMap(state.requestTokenByCaseId, () => null),
        requestDraftByCaseId: clearSessionMap(state.requestDraftByCaseId, () => undefined)
      };
  }
}

const actionMatrix: Record<ReviewCaseType, readonly DecisionAction[]> = {
  person_identity: ["approve", "correct", "link"],
  author_identity: ["approve", "correct", "link"],
  product: ["approve", "correct"],
  project_director_relation: ["approve", "correct", "link"],
  external_identity: ["approve", "correct", "link"],
  possible_duplicate: ["link"],
  invalid_text: [],
  new_evidence_conflict: []
};

const internalReference = /^(?:b2b:v1:|dropbox_path:|s3:|minio:|file:)|(?:^|[._-])(?:document|source|object|bucket)_(?:key|path)(?:$|[:._-])/i;
const publicReference = /^[\p{L}\p{N}][\p{L}\p{N}:._@-]*$/u;

export class DecisionValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DecisionValidationError";
  }
}

export function allowedDecisionActions(caseType: ReviewCaseType): DecisionAction[] {
  return [...actionMatrix[caseType]];
}

export function isPublicIdentityReference(value: string | null | undefined): boolean {
  const candidate = value?.trim() ?? "";
  return Boolean(candidate)
    && candidate.length <= 320
    && publicReference.test(candidate)
    && !internalReference.test(candidate);
}

function publicReferenceFromDetail(detail: CaseDetail): string {
  const candidate = detail.canonical_value;
  return isPublicIdentityReference(candidate) ? candidate!.trim() : "";
}

function preferredText(detail: CaseDetail): string {
  return (detail.canonical_value ?? detail.normalized_value ?? detail.detected_value ?? "").trim();
}

function defaultScope(caseType: ReviewCaseType): DecisionScope {
  if (caseType === "person_identity" || caseType === "author_identity" || caseType === "external_identity") return "global_identity";
  if (caseType === "project_director_relation") return "relationship";
  return "record";
}

function defaultPayload(detail: CaseDetail, action: DecisionAction): DecisionPayload | null {
  const reference = publicReferenceFromDetail(detail);
  const text = preferredText(detail);
  switch (detail.case_type) {
    case "person_identity":
    case "author_identity":
      return {
        case_type: detail.case_type,
        canonical_identity_key: detail.field_path === "canonical_identity_key" ? reference || null : null,
        canonical_name: detail.field_path === "canonical_name" ? text : text,
        aliases: [],
        scientific_status: "validated",
        ...(action === "link" ? { resolution: "linked" as LinkResolution } : {})
      };
    case "product":
      return { case_type: "product", product_title: text || null, scientific_status: "validated" };
    case "project_director_relation":
      return {
        case_type: "project_director_relation",
        project_director_identity_key: detail.field_path === "project_director_identity_key" ? reference || null : null,
        relationship_status: "linked",
        scientific_status: "validated"
      };
    case "external_identity":
      return {
        case_type: "external_identity",
        external_identity_key: detail.field_path === "external_identity_key" ? reference || null : null,
        external_institution: detail.field_path === "external_institution" ? text || null : null,
        scientific_status: "validated",
        ...(action === "link" ? { resolution: "linked" as LinkResolution } : {})
      };
    case "possible_duplicate":
      return detail.counterpart_options[0]
        ? {
            case_type: "possible_duplicate",
            counterpart_ref: { ...detail.counterpart_options[0].counterpart_ref },
            resolution: "merged",
            scientific_status: "validated"
          }
        : null;
    case "invalid_text":
    case "new_evidence_conflict":
      return null;
  }
}

export function createDecisionDraft(
  detail: CaseDetail,
  action: DecisionAction,
  correlationId: string
): DecisionDraft | null {
  if (!actionMatrix[detail.case_type].includes(action)) return null;
  const payload = defaultPayload(detail, action);
  if (!payload) return null;
  return {
    action,
    scope: defaultScope(detail.case_type),
    payload,
    reason: "",
    correlation_id: correlationId
  };
}

export function withDecisionPayload(draft: DecisionDraft, payload: DecisionPayload): DecisionDraft {
  const clonedPayload = payload.case_type === "person_identity" || payload.case_type === "author_identity"
    ? { ...payload, aliases: payload.aliases ? [...payload.aliases] : [] }
    : { ...payload };
  return { ...draft, payload: clonedPayload };
}

export function withDecisionAction(
  detail: CaseDetail,
  draft: DecisionDraft,
  action: DecisionAction
): DecisionDraft | null {
  if (draft.payload.case_type !== detail.case_type || !actionMatrix[detail.case_type].includes(action)) return null;
  let payload = withDecisionPayload(draft, draft.payload).payload;
  if (payload.case_type === "person_identity" || payload.case_type === "author_identity" || payload.case_type === "external_identity") {
    if (action === "link") payload = { ...payload, resolution: payload.resolution ?? "linked" };
    else {
      const { resolution: _resolution, ...withoutResolution } = payload;
      payload = withoutResolution;
    }
  }
  return { ...draft, action, payload };
}

export function cancelDecisionDraft(): null {
  return null;
}

function proposedValue(payload: DecisionPayload): string | null {
  switch (payload.case_type) {
    case "person_identity":
    case "author_identity":
      return payload.canonical_name.trim() || null;
    case "product":
      return payload.product_title?.trim() || null;
    case "project_director_relation":
      return payload.project_director_identity_key?.trim() || payload.relationship_status;
    case "external_identity":
      return payload.external_identity_key?.trim() || payload.external_institution?.trim() || "Se asignará automáticamente al confirmar";
    case "possible_duplicate":
      return payload.resolution;
  }
}

const resolutionLabels: Record<LinkResolution | DuplicateResolution, string> = {
  linked: "Vinculado",
  merged: "Fusionado",
  maintained_separate: "Mantener separado",
  separated: "Separado"
};

function sameCounterpartReference(left: CounterpartReference, right: CounterpartReference): boolean {
  return left.target_type === right.target_type && left.target_id === right.target_id;
}

function selectedCounterpart(detail: CaseDetail, payload: DuplicatePayload): CounterpartOption | null {
  return detail.counterpart_options.find((option) => sameCounterpartReference(option.counterpart_ref, payload.counterpart_ref)) ?? null;
}

function proposedFields(payload: DecisionPayload, detail: CaseDetail): { label: string; value: string | null }[] {
  const status = { label: "Estado científico", value: "Validado" };
  switch (payload.case_type) {
    case "person_identity":
    case "author_identity":
      return [
        {
          label: "Referencia de identidad",
          value: payload.canonical_identity_key || "Se asignará automáticamente al confirmar"
        },
        { label: "Nombre canónico", value: payload.canonical_name },
        { label: "Alias", value: payload.aliases?.join(", ") || null },
        ...(payload.resolution ? [{ label: "Resolución", value: resolutionLabels[payload.resolution] }] : []),
        status
      ];
    case "product":
      return [{ label: "Título del producto", value: payload.product_title ?? null }, status];
    case "project_director_relation":
      return [
        { label: "Identificador canónico público del director", value: payload.project_director_identity_key ?? null },
        { label: "Resolución", value: resolutionLabels[payload.relationship_status] },
        status
      ];
    case "external_identity":
      return [
        { label: "Identificador canónico público", value: payload.external_identity_key || "Se asignará automáticamente al confirmar" },
        { label: "Institución externa", value: payload.external_institution ?? null },
        ...(payload.resolution ? [{ label: "Resolución", value: resolutionLabels[payload.resolution] }] : []),
        status
      ];
    case "possible_duplicate":
      return [
        { label: "Contraparte seleccionada", value: selectedCounterpart(detail, payload)?.display_name ?? null },
        { label: "Resolución", value: resolutionLabels[payload.resolution] },
        status
      ];
  }
}

function selectedPublicLink(payload: DecisionPayload, detail: CaseDetail): string | null {
  if (payload.case_type === "person_identity" || payload.case_type === "author_identity") return payload.canonical_identity_key || null;
  if (payload.case_type === "external_identity") return payload.external_identity_key || null;
  if (payload.case_type === "project_director_relation") return payload.project_director_identity_key ?? null;
  if (payload.case_type === "possible_duplicate") return selectedCounterpart(detail, payload)?.display_name ?? null;
  return null;
}

export function buildDecisionPreview(detail: CaseDetail, draft: DecisionDraft): DecisionPreviewModel {
  const currentValue = detail.canonical_value ?? detail.normalized_value ?? detail.detected_value;
  const nextValue = proposedValue(draft.payload);
  const changedFields: string[] = [];
  if ((currentValue ?? "").trim() !== (nextValue ?? "").trim()) changedFields.push(detail.field_path === "case" ? "valor revisado" : detail.field_path);
  if (draft.action === "link") changedFields.push("vínculo");
  return {
    action: draft.action,
    scope: draft.scope,
    currentValue,
    proposedValue: nextValue,
    changedFields: [...new Set(changedFields)],
    selectedLink: draft.action === "link" ? selectedPublicLink(draft.payload, detail) : null,
    proposedFields: proposedFields(draft.payload, detail),
    possibleKpiImpact: detail.possible_kpi_impact
  };
}

export function isOptimisticVersionConflict(error: unknown): error is OptimisticConflictError {
  if (!error || typeof error !== "object") return false;
  const candidate = error as Partial<OptimisticConflictError>;
  return candidate.status === 409 && candidate.code === "REVIEW_CASE_VERSION_CONFLICT";
}

export function isValidHumanReviewReload(caseId: string, detail: unknown): detail is CaseDetail {
  if (!detail || typeof detail !== "object") return false;
  const candidate = detail as Partial<CaseDetail>;
  return candidate.id === caseId
    && Number.isInteger(candidate.version)
    && (candidate.version ?? -1) >= 1
    && (typeof candidate.current_decision_id === "string" || candidate.current_decision_id === null)
    && typeof candidate.case_status === "string"
    && reviewCaseStatuses.includes(candidate.case_status as ReviewCaseStatus);
}

export function isDraftCompatibleWithDetail(detail: CaseDetail, draft: DecisionDraft | null): boolean {
  if (!draft || draft.payload.case_type !== detail.case_type || !actionMatrix[detail.case_type].includes(draft.action)) return false;
  if (draft.payload.case_type !== "possible_duplicate") return true;
  const counterpartRef = draft.payload.counterpart_ref;
  return detail.counterpart_options.some((option) =>
    option.counterpart_ref.target_type === counterpartRef.target_type
    && option.counterpart_ref.target_id === counterpartRef.target_id
  );
}

export function createOptimisticConflictSnapshot(
  detail: CaseDetail,
  draft: DecisionDraft | null,
  error: OptimisticConflictError,
  operation: OptimisticConflictOperation = "apply",
  revertDecisionId: string | null = null
): OptimisticConflictSnapshot {
  return {
    operation,
    revert_decision_id: operation === "revert" ? revertDecisionId : null,
    draft,
    preview: draft ? buildDecisionPreview(detail, draft) : null,
    cas: { expected_version: detail.version, expected_current_decision_id: detail.current_decision_id },
    correlation_id: error.correlation_id,
    reload_error_correlation_id: null,
    requires_manual_confirmation: true,
    command_retried: false,
    needs_revision: false,
    awaiting_reload: true,
    operation_abandoned: false
  };
}

export function isOptimisticConflictOperationCompatible(detail: CaseDetail, snapshot: OptimisticConflictSnapshot): boolean {
  if (snapshot.operation === "apply") return isEditableReviewStatus(detail.case_status) && isDraftCompatibleWithDetail(detail, snapshot.draft);
  if (snapshot.operation === "discard") return isEditableReviewStatus(detail.case_status);
  return detail.case_status === "resolved"
    && Boolean(snapshot.revert_decision_id)
    && detail.current_decision_id === snapshot.revert_decision_id;
}

export function isOptimisticConfirmationBlocked(snapshot: OptimisticConflictSnapshot | null): boolean {
  return Boolean(snapshot?.awaiting_reload || snapshot?.needs_revision);
}

export function keepOptimisticConflictAwaitingReload(snapshot: OptimisticConflictSnapshot): OptimisticConflictSnapshot {
  return { ...snapshot, awaiting_reload: true, command_retried: false };
}

export function abandonOptimisticConflictOperation(snapshot: OptimisticConflictSnapshot): OptimisticConflictSnapshot {
  if (!snapshot.awaiting_reload) return snapshot;
  return {
    ...snapshot,
    draft: null,
    preview: null,
    operation_abandoned: true,
    awaiting_reload: true,
    command_retried: false
  };
}

export function updateOptimisticConflictDraft(
  snapshot: OptimisticConflictSnapshot,
  detail: CaseDetail,
  draft: DecisionDraft
): OptimisticConflictSnapshot {
  const next = { ...snapshot, draft, preview: buildDecisionPreview(detail, draft) };
  return { ...next, needs_revision: !isOptimisticConflictOperationCompatible(detail, next) };
}

export function applyOptimisticConflictReload(
  snapshot: OptimisticConflictSnapshot,
  detail: CaseDetail
): OptimisticConflictSnapshot {
  const compatible = snapshot.operation_abandoned || isOptimisticConflictOperationCompatible(detail, snapshot);
  return {
    ...snapshot,
    preview: snapshot.draft ? buildDecisionPreview(detail, snapshot.draft) : null,
    cas: { expected_version: detail.version, expected_current_decision_id: detail.current_decision_id },
    reload_error_correlation_id: null,
    requires_manual_confirmation: true,
    command_retried: false,
    needs_revision: !compatible,
    awaiting_reload: false
  };
}

export function keepOptimisticConflictAfterReloadFailure(
  snapshot: OptimisticConflictSnapshot,
  error: OptimisticConflictError
): OptimisticConflictSnapshot {
  return { ...snapshot, reload_error_correlation_id: error.correlation_id, command_retried: false, awaiting_reload: true };
}

function validatedReason(reason: string, required: boolean): string | null {
  const value = reason.trim();
  if (value.length > MAX_DECISION_REASON_LENGTH) throw new DecisionValidationError("El motivo no puede superar 4000 caracteres.");
  if (required && !value) throw new DecisionValidationError("El motivo es obligatorio para esta decisión.");
  return value || null;
}

function sanitizedPayload(payload: DecisionPayload, detail: CaseDetail): DecisionPayload {
  if (payload.scientific_status !== "validated") {
    throw new DecisionValidationError("El estado científico debe permanecer validado para aplicar una decisión.");
  }
  switch (payload.case_type) {
    case "person_identity":
    case "author_identity": {
      const key = payload.canonical_identity_key?.trim() || null;
      const name = payload.canonical_name.trim();
      if (key && !isPublicIdentityReference(key)) throw new DecisionValidationError("Ingresa un identificador canónico público válido.");
      if (!name || name.length > 500) throw new DecisionValidationError("Ingresa un nombre canónico válido de hasta 500 caracteres.");
      const aliases = (payload.aliases ?? []).map((alias) => alias.trim()).filter(Boolean);
      if (aliases.length > 100 || aliases.some((alias) => alias.length > 500) || new Set(aliases).size !== aliases.length)
        throw new DecisionValidationError("Los alias deben ser únicos y respetar los límites del contrato.");
      return {
        case_type: payload.case_type,
        canonical_identity_key: key,
        canonical_name: name,
        aliases,
        scientific_status: payload.scientific_status,
        ...(payload.resolution ? { resolution: payload.resolution } : {})
      };
    }
    case "product": {
      const title = payload.product_title?.trim() || null;
      if (title && title.length > 1000) throw new DecisionValidationError("El título no puede superar 1000 caracteres.");
      return { case_type: "product", product_title: title, scientific_status: payload.scientific_status };
    }
    case "project_director_relation": {
      const key = payload.project_director_identity_key?.trim() || null;
      if (key && !isPublicIdentityReference(key)) throw new DecisionValidationError("Ingresa un identificador canónico público válido.");
      return {
        case_type: "project_director_relation",
        project_director_identity_key: key,
        relationship_status: payload.relationship_status,
        scientific_status: payload.scientific_status
      };
    }
    case "external_identity": {
      const key = payload.external_identity_key?.trim() || null;
      const institution = payload.external_institution?.trim() || null;
      if (key && !isPublicIdentityReference(key)) throw new DecisionValidationError("Ingresa un identificador canónico público válido.");
      if (institution && institution.length > 500) throw new DecisionValidationError("La institución no puede superar 500 caracteres.");
      return {
        case_type: "external_identity",
        external_identity_key: key,
        external_institution: institution,
        scientific_status: payload.scientific_status,
        ...(payload.resolution ? { resolution: payload.resolution } : {})
      };
    }
    case "possible_duplicate": {
      const counterpart = selectedCounterpart(detail, payload);
      if (!counterpart || !Number.isSafeInteger(payload.counterpart_ref.target_id) || payload.counterpart_ref.target_id <= 0) {
        throw new DecisionValidationError("Selecciona una contraparte pública válida para este caso.");
      }
      return {
        case_type: "possible_duplicate",
        counterpart_ref: { ...counterpart.counterpart_ref },
        resolution: payload.resolution,
        scientific_status: payload.scientific_status
      };
    }
  }
}

export function buildApplyDecisionRequest(detail: CaseDetail, draft: DecisionDraft): ApplyDecisionRequest {
  if (draft.payload.case_type !== detail.case_type || !actionMatrix[detail.case_type].includes(draft.action))
    throw new DecisionValidationError("La acción no es compatible con el tipo de caso.");
  if (draft.action === "link") {
    const resolution = draft.payload.case_type === "project_director_relation"
      ? draft.payload.relationship_status
      : draft.payload.case_type === "person_identity" || draft.payload.case_type === "author_identity" || draft.payload.case_type === "external_identity" || draft.payload.case_type === "possible_duplicate"
        ? draft.payload.resolution
        : null;
    if (!resolution) throw new DecisionValidationError("Selecciona una resolución de vínculo válida.");
  }
  const preview = buildDecisionPreview(detail, draft);
  const reason = validatedReason(draft.reason, draft.action !== "approve" || preview.changedFields.length > 0);
  return {
    expected_version: detail.version,
    expected_current_decision_id: detail.current_decision_id,
    action: draft.action,
    scope: draft.scope,
    payload: sanitizedPayload(draft.payload, detail),
    reason,
    correlation_id: draft.correlation_id
  };
}

export function validateSessionDraft(
  detail: CaseDetail | undefined,
  draft: DecisionDraft | undefined,
  conflict?: OptimisticConflictSnapshot
): string[] {
  if (!detail) return ["No se pudo cargar la revisión actual."];
  if (!draft) return ["Prepara una decisión antes de confirmar."];
  if (conflict && isOptimisticConfirmationBlocked(conflict)) {
    return ["Recarga y revisa este caso antes de volver a confirmar."];
  }
  if (!isEditableReviewStatus(detail.case_status)) return ["La revisión no está en un estado editable."];
  try {
    buildApplyDecisionRequest(detail, draft);
    return [];
  } catch (error) {
    return [error instanceof DecisionValidationError
      ? error.message
      : "La decisión contiene datos que deben corregirse."];
  }
}

export function buildDiscardRequest(detail: CaseDetail, reason: string, correlationId: string): DiscardRequest {
  return {
    expected_version: detail.version,
    expected_current_decision_id: detail.current_decision_id,
    reason: validatedReason(reason, true)!,
    correlation_id: correlationId
  };
}

export function buildRevertRequest(detail: CaseDetail, reason: string, correlationId: string, decisionIdToRevert = detail.current_decision_id): RevertRequest {
  if (!detail.current_decision_id) throw new DecisionValidationError("No existe una decisión vigente que pueda revertirse.");
  if (decisionIdToRevert !== detail.current_decision_id) throw new DecisionValidationError("La decisión vigente cambió y debe revisarse antes de revertir.");
  return {
    expected_version: detail.version,
    expected_current_decision_id: detail.current_decision_id,
    decision_id_to_revert: decisionIdToRevert,
    reason: validatedReason(reason, true)!,
    correlation_id: correlationId
  };
}

export type HumanReviewErrorCode =
  | "HUMAN_REVIEW_VALIDATION"
  | "AUTHENTICATION_REQUIRED"
  | "B2B_CAPABILITY_REQUIRED"
  | "REVIEW_CASE_NOT_FOUND"
  | "REVIEW_CASE_VERSION_CONFLICT"
  | "INCOMPATIBLE_DECISION"
  | "INVALID_COMMAND_PAYLOAD"
  | "HUMAN_REVIEW_INTERNAL_ERROR"
  | "EVIDENCE_UNAVAILABLE";

export type HumanReviewErrorDetails = {
  field?: PublicScalar;
  current_status?: PublicScalar;
  target_status?: PublicScalar;
  case_type?: PublicScalar;
  action?: PublicScalar;
  resolution?: PublicScalar;
  scope?: PublicScalar;
  expected_version?: PublicScalar;
  actual_version?: PublicScalar;
};
