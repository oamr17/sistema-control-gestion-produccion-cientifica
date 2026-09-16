"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { ReviewPagination, getPaginationModel, isQueuePageReconciling, reconcileQueuePage } from "@/components/human-review/ReviewPagination";
import { actionableQueueCaseTypes, actionableQueueStatuses, periodIdForSelection, ReviewQueueFilters, withQueuePageSize } from "@/components/human-review/ReviewQueueFilters";
import { ReviewQueueTable } from "@/components/human-review/ReviewQueueTable";
import { useHumanReviewQueue } from "@/hooks/useHumanReview";
import { useDataCache } from "@/lib/data-cache";
import { useGlobalFilters } from "@/lib/filters";
import type { QueueQuery } from "@/lib/human-review";

const initialQuery: QueueQuery = {
  page: 1,
  page_size: 25,
  statuses: actionableQueueStatuses,
  case_types: actionableQueueCaseTypes,
  sort: "priority_oldest"
};

function hasDefaultActionableStatuses(statuses: QueueQuery["statuses"]) {
  return (
    statuses.length === actionableQueueStatuses.length &&
    actionableQueueStatuses.every((status) => statuses.includes(status))
  );
}

function hasDefaultActionableCaseTypes(caseTypes: QueueQuery["case_types"]) {
  return (
    caseTypes.length === actionableQueueCaseTypes.length &&
    actionableQueueCaseTypes.every((caseType) => caseTypes.includes(caseType))
  );
}

function hasActiveFilters(query: QueueQuery) {
  return Boolean(
    !hasDefaultActionableStatuses(query.statuses) || !hasDefaultActionableCaseTypes(query.case_types) || query.period_id || query.document_id ||
    query.source_revision || query.created_from || query.created_to || query.q
  );
}

export default function HumanReviewPage() {
  const [query, setQuery] = useState<QueueQuery>(initialQuery);
  const { periods, yearLabel, cycle } = useGlobalFilters();
  const selectedPeriodId = periodIdForSelection(periods, yearLabel, cycle);
  const queue = useHumanReviewQueue(query);
  const cache = useDataCache();
  const responsePage = queue.data?.page ?? query.page;
  const responsePageSize = queue.data?.page_size ?? query.page_size;
  const pagination = getPaginationModel(queue.data?.total ?? 0, responsePage, responsePageSize);
  const isPageOutOfRange = isQueuePageReconciling(query, queue.data);

  useEffect(() => {
    const response = queue.data;
    if (!response) return;
    setQuery((current) => reconcileQueuePage(current, response));
  }, [queue.data]);

  useEffect(() => {
    setQuery((current) => {
      if (current.period_id === selectedPeriodId) return current;
      const next: QueueQuery = { ...current, page: 1 };
      if (selectedPeriodId === undefined) delete next.period_id;
      else next.period_id = selectedPeriodId;
      return next;
    });
  }, [selectedPeriodId]);

  function changePage(page: number) {
    const nextPage = Math.max(1, Math.min(pagination.totalPages, page));
    setQuery((current) => ({ ...current, page: nextPage }));
  }

  function refresh() {
    cache.invalidate("human-review:queue:");
  }

  return (
    <AppShell hideCareerFilter>
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">{"Control cient\u00edfico"}</p>
          <h2 className="mt-2 text-3xl font-semibold text-ink">{"Revisi\u00f3n humana"}</h2>
          <p className="mt-2 max-w-2xl text-sm text-ink/60">{"Casos ordenados por prioridad y antig\u00fcedad para su revisi\u00f3n operativa."}</p>
        </div>
        <button type="button" onClick={refresh} disabled={queue.isInitialLoading || queue.isUpdating} className="focus-ring inline-flex items-center justify-center gap-2 rounded-[8px] border border-line bg-white px-4 py-3 text-sm font-semibold text-ink shadow-soft disabled:cursor-not-allowed disabled:opacity-50">
          <RefreshCw size={17} aria-hidden="true" />
          {queue.isUpdating ? "Actualizando" : "Actualizar cola"}
        </button>
      </header>

      <section className="mt-6" aria-labelledby="queue-filter-heading">
        <h3 id="queue-filter-heading" className="sr-only">Filtros de la bandeja</h3>
        <ReviewQueueFilters query={query} onApply={setQuery} isApplying={queue.isInitialLoading || queue.isUpdating} />
      </section>

      <section className="mt-6 space-y-4" aria-labelledby="queue-results-heading">
        <div className="flex items-center justify-between gap-3">
          <h3 id="queue-results-heading" className="text-lg font-semibold text-ink">Casos</h3>
          {queue.data ? <p className="text-sm text-ink/60">{queue.data.total} resultados</p> : null}
        </div>
        <ReviewQueueTable
          data={queue.data}
          error={queue.error}
          isInitialLoading={queue.isInitialLoading || isPageOutOfRange}
          isUpdating={queue.isUpdating}
          hasActiveFilters={hasActiveFilters(query)}
        />
        {queue.data && !isPageOutOfRange ? (
          <ReviewPagination
            total={queue.data.total}
            page={responsePage}
            pageSize={responsePageSize}
            onPageChange={changePage}
            onPageSizeChange={(pageSize) => setQuery((current) => withQueuePageSize(current, pageSize))}
          />
        ) : null}
      </section>
    </AppShell>
  );
}
