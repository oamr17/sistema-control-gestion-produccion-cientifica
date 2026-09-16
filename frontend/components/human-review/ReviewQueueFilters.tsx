"use client";

import { useEffect, useState } from "react";

import type { QueueQuery, ReviewCaseStatus, ReviewCaseType } from "../../lib/human-review";
import type { Period } from "../../lib/types";

const SEARCH_MIN_LENGTH = 3;
const SEARCH_MAX_LENGTH = 128;
const SOURCE_REVISION_MAX_LENGTH = 120;

export const actionableQueueStatuses: ReviewCaseStatus[] = [
  "pending",
  "in_review",
  "awaiting_gestor_approval",
  "reopened",
  "conflicted"
];

export const actionableQueueCaseTypes: ReviewCaseType[] = [
  "person_identity",
  "author_identity",
  "product",
  "project_director_relation",
  "external_identity",
  "possible_duplicate"
];

const statusOptions: { value: ReviewCaseStatus; label: string }[] = [
  { value: "pending", label: "Pendiente" },
  { value: "in_review", label: "En revisi\u00f3n" },
  { value: "awaiting_gestor_approval", label: "Esperando aprobaci\u00f3n" },
  { value: "resolved", label: "Resuelto" },
  { value: "reopened", label: "Reabierto" },
  { value: "conflicted", label: "En conflicto" },
  { value: "superseded", label: "Reemplazado" }
];

const caseTypeOptions: { value: ReviewCaseType; label: string }[] = [
  { value: "person_identity", label: "Identidad de persona" },
  { value: "author_identity", label: "Identidad de autor" },
  { value: "product", label: "Producto" },
  { value: "project_director_relation", label: "Relaci\u00f3n con director" },
  { value: "external_identity", label: "Identidad externa" },
  { value: "possible_duplicate", label: "Posible duplicado" },
  { value: "invalid_text", label: "Texto inv\u00e1lido" },
  { value: "new_evidence_conflict", label: "Conflicto de evidencia" }
];

type QueueQueryInput = Partial<QueueQuery> & Record<string, unknown>;

function normalizedString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function positiveInteger(value: unknown): number | undefined {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : undefined;
}

function normalizedSearch(value: unknown): string | undefined {
  const normalized = normalizedString(value);
  if (!normalized || normalized.length < SEARCH_MIN_LENGTH || normalized.length > SEARCH_MAX_LENGTH) {
    return undefined;
  }
  return normalized;
}

function normalizedSourceRevision(value: unknown): string | undefined {
  const normalized = normalizedString(value);
  return normalized && normalized.length <= SOURCE_REVISION_MAX_LENGTH ? normalized : undefined;
}

export function normalizeQueueQuery(input: QueueQueryInput): QueueQuery {
  const normalized: QueueQuery = {
    page: Math.max(1, Math.floor(Number(input.page) || 1)),
    page_size: Math.max(1, Math.min(100, Math.floor(Number(input.page_size) || 25))),
    statuses: Array.isArray(input.statuses) ? [...new Set(input.statuses)] as ReviewCaseStatus[] : [],
    case_types: Array.isArray(input.case_types) ? [...new Set(input.case_types)] as ReviewCaseType[] : [],
    sort: "priority_oldest"
  };

  const periodId = positiveInteger(input.period_id);
  const documentId = positiveInteger(input.document_id);
  const sourceRevision = normalizedSourceRevision(input.source_revision);
  const createdFrom = normalizedString(input.created_from);
  const createdTo = normalizedString(input.created_to);
  const q = normalizedSearch(input.q);
  if (periodId !== undefined) normalized.period_id = periodId;
  if (documentId !== undefined) normalized.document_id = documentId;
  if (sourceRevision !== undefined) normalized.source_revision = sourceRevision;
  if (createdFrom !== undefined) normalized.created_from = createdFrom;
  if (createdTo !== undefined) normalized.created_to = createdTo;
  if (q !== undefined) normalized.q = q;
  return normalized;
}

export function withQueueFilters(current: QueueQuery, updates: QueueQueryInput): QueueQuery {
  return normalizeQueueQuery({ ...current, ...updates, page: 1 });
}

export function withQueuePageSize(current: QueueQuery, pageSize: number): QueueQuery {
  return normalizeQueueQuery({ ...current, page: 1, page_size: pageSize });
}

type FilterFormState = {
  statuses: ReviewCaseStatus[];
  caseTypes: ReviewCaseType[];
  documentId: string;
  sourceRevision: string;
  createdFrom: string;
  createdTo: string;
  q: string;
};

function dateTimeInputValue(value: string | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const offset = parsed.getTimezoneOffset() * 60_000;
  return new Date(parsed.getTime() - offset).toISOString().slice(0, 16);
}

function initialFormState(query: QueueQuery): FilterFormState {
  return {
    statuses: query.statuses,
    caseTypes: query.case_types,
    documentId: query.document_id === undefined ? "" : String(query.document_id),
    sourceRevision: query.source_revision ?? "",
    createdFrom: dateTimeInputValue(query.created_from),
    createdTo: dateTimeInputValue(query.created_to),
    q: query.q ?? ""
  };
}

export function periodIdForSelection(
  periods: readonly Period[],
  yearLabel: string,
  cycle: number
): number | undefined {
  return periods.find((period) => period.year_label === yearLabel && period.cycle === cycle)?.id;
}

export function resetQueueFilters(query: QueueQuery): QueueQuery {
  return normalizeQueueQuery({
    page: 1,
    page_size: query.page_size,
    period_id: query.period_id,
    statuses: []
  });
}

function isoDate(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

export function ReviewQueueFilters({
  query,
  onApply,
  isApplying = false
}: {
  query: QueueQuery;
  onApply: (query: QueueQuery) => void;
  isApplying?: boolean;
}) {
  const [form, setForm] = useState<FilterFormState>(() => initialFormState(query));
  const [searchError, setSearchError] = useState("");

  useEffect(() => {
    setForm(initialFormState(query));
  }, [query]);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const search = form.q.trim();
    if (search && (search.length < SEARCH_MIN_LENGTH || search.length > SEARCH_MAX_LENGTH)) {
      setSearchError(`La b\u00fasqueda debe tener entre ${SEARCH_MIN_LENGTH} y ${SEARCH_MAX_LENGTH} caracteres.`);
      return;
    }
    setSearchError("");
    onApply(withQueueFilters(query, {
      statuses: form.statuses,
      case_types: form.caseTypes,
      document_id: form.documentId ? Number(form.documentId) : undefined,
      source_revision: form.sourceRevision || undefined,
      created_from: isoDate(form.createdFrom),
      created_to: isoDate(form.createdTo),
      q: search || undefined
    }));
  }

  function reset() {
    const normalized = resetQueueFilters(query);
    const cleared = initialFormState(normalized);
    setForm(cleared);
    setSearchError("");
    onApply(normalized);
  }

  return (
    <form onSubmit={submit} className="rounded-[8px] border border-line bg-white p-4 shadow-soft" aria-label={"Filtros de revisi\u00f3n humana"}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <label className="text-sm font-medium text-ink">
          Estados
          <select
            multiple
            value={form.statuses}
            onChange={(event) => {
              const selectedStatuses = Array.from(
                event.currentTarget.selectedOptions,
                (option) => option.value as ReviewCaseStatus
              );
              setForm((current) => ({ ...current, statuses: selectedStatuses }));
            }}
            className="focus-ring mt-2 min-h-24 w-full rounded-[8px] border border-line bg-white px-3 py-2 text-sm"
          >
            {statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium text-ink">
          Tipos de caso
          <select
            multiple
            value={form.caseTypes}
            onChange={(event) => {
              const selectedCaseTypes = Array.from(
                event.currentTarget.selectedOptions,
                (option) => option.value as ReviewCaseType
              );
              setForm((current) => ({ ...current, caseTypes: selectedCaseTypes }));
            }}
            className="focus-ring mt-2 min-h-24 w-full rounded-[8px] border border-line bg-white px-3 py-2 text-sm"
          >
            {caseTypeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <div className="grid content-start gap-4">
          <label className="text-sm font-medium text-ink">
            Documento
            <input type="number" min={1} value={form.documentId} onChange={(event) => setForm((current) => ({ ...current, documentId: event.target.value }))} className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-2" />
          </label>
        </div>
        <div className="grid content-start gap-4">
          <label className="text-sm font-medium text-ink">
            {"Revisi\u00f3n de origen"}
            <input value={form.sourceRevision} maxLength={SOURCE_REVISION_MAX_LENGTH} onChange={(event) => setForm((current) => ({ ...current, sourceRevision: event.target.value }))} className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-ink">
            Buscar
            <input value={form.q} minLength={SEARCH_MIN_LENGTH} maxLength={SEARCH_MAX_LENGTH} placeholder="Documento o destino estable" onChange={(event) => setForm((current) => ({ ...current, q: event.target.value }))} aria-describedby={searchError ? "human-review-search-error" : undefined} className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-2" />
          </label>
        </div>
        <label className="text-sm font-medium text-ink">
          Creado desde
          <input type="datetime-local" value={form.createdFrom} onChange={(event) => setForm((current) => ({ ...current, createdFrom: event.target.value }))} className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-2" />
        </label>
        <label className="text-sm font-medium text-ink">
          Creado hasta
          <input type="datetime-local" value={form.createdTo} onChange={(event) => setForm((current) => ({ ...current, createdTo: event.target.value }))} className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-2" />
        </label>
      </div>
      {searchError ? <p id="human-review-search-error" role="alert" className="mt-3 text-sm font-medium text-coral">{searchError}</p> : null}
      <div className="mt-4 flex flex-wrap justify-end gap-3">
        <button type="button" onClick={reset} className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink">Limpiar filtros</button>
        <button type="submit" disabled={isApplying} className="focus-ring rounded-[8px] bg-mint px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Aplicar filtros</button>
      </div>
    </form>
  );
}
