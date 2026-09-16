"use client";

import type { QueueQuery, QueueResponse } from "../../lib/human-review";

export type PaginationModel = {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  canPrevious: boolean;
  canNext: boolean;
};

export function getPaginationModel(total: number, page: number, pageSize: number): PaginationModel {
  const safeTotal = Math.max(0, Math.floor(total || 0));
  const safePageSize = Math.max(1, Math.floor(pageSize || 25));
  const totalPages = Math.max(1, Math.ceil(safeTotal / safePageSize));
  const safePage = Math.max(1, Math.min(totalPages, Math.floor(page || 1)));
  return {
    page: safePage,
    pageSize: safePageSize,
    total: safeTotal,
    totalPages,
    canPrevious: safePage > 1,
    canNext: safePage < totalPages
  };
}

export function reconcileQueuePage(query: QueueQuery, response: Pick<QueueResponse, "total" | "page_size">): QueueQuery {
  const totalPages = Math.max(1, Math.ceil(response.total / response.page_size));
  if (query.page <= totalPages) return query;
  return { ...query, page: totalPages };
}

export function isQueuePageReconciling(
  query: QueueQuery,
  response: Pick<QueueResponse, "page" | "page_size" | "total"> | undefined
): boolean {
  if (!response) return false;
  const totalPages = Math.max(1, Math.ceil(response.total / response.page_size));
  return response.page !== query.page || query.page > totalPages;
}

export function ReviewPagination({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange
}: {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const pagination = getPaginationModel(total, page, pageSize);
  return (
    <div className="flex flex-col gap-3 rounded-[8px] border border-line bg-white px-4 py-3 text-sm shadow-soft sm:flex-row sm:items-center sm:justify-between">
      <p className="text-ink/60">{pagination.total} {"casos \u00b7 P\u00e1gina"} {pagination.page} de {pagination.totalPages}</p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-ink/70">
          {"Por p\u00e1gina"}
          <select value={pagination.pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))} className="focus-ring rounded-[8px] border border-line bg-white px-2 py-2" aria-label={"Casos por p\u00e1gina"}>
            {[10, 25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
        <button type="button" disabled={!pagination.canPrevious} onClick={() => onPageChange(Math.max(1, pagination.page - 1))} className="focus-ring rounded-[8px] border border-line px-3 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-45">Anterior</button>
        <button type="button" disabled={!pagination.canNext} onClick={() => onPageChange(Math.min(pagination.totalPages, pagination.page + 1))} className="focus-ring rounded-[8px] border border-line px-3 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-45">Siguiente</button>
      </div>
    </div>
  );
}
