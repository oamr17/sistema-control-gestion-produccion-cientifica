"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

type DataTableProps<T> = {
  columns: { key: keyof T | string; label: string; render?: (item: T) => React.ReactNode }[];
  rows: T[];
  renderExpanded?: (row: T) => React.ReactNode;
  pageSize?: number;
};

export function DataTable<T extends { id: number }>({ columns, rows, renderExpanded, pageSize = 10 }: DataTableProps<T>) {
  const [openRowId, setOpenRowId] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const paginatedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return rows.slice(start, start + pageSize);
  }, [page, pageSize, rows]);

  useEffect(() => {
    setPage(1);
    setOpenRowId(null);
  }, [rows]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  function toggleRow(rowId: number) {
    setOpenRowId((current) => (current === rowId ? null : rowId));
  }

  return (
    <div className="min-w-0 overflow-hidden rounded-[8px] border border-line bg-white shadow-soft">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-line text-sm">
          <thead className="bg-paper">
            <tr>
              {renderExpanded ? <th className="w-12 px-4 py-3 text-left font-semibold text-ink/70">Detalle</th> : null}
              {columns.map((column) => (
                <th key={String(column.key)} className="px-4 py-3 text-left font-semibold text-ink/70">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {paginatedRows.map((row) => {
              const isOpen = openRowId === row.id;
              return (
                <Fragment key={row.id}>
                  <tr key={row.id} className="hover:bg-paper/70">
                    {renderExpanded ? (
                      <td className="px-4 py-3 text-ink/70">
                        <button
                          type="button"
                          onClick={() => toggleRow(row.id)}
                          className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-[8px] border border-line bg-white"
                          aria-label={isOpen ? "Ocultar detalle" : "Ver detalle"}
                        >
                          {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        </button>
                      </td>
                    ) : null}
                    {columns.map((column) => (
                      <td key={String(column.key)} className="px-4 py-3 text-ink/75">
                        {column.render ? column.render(row) : String(row[column.key as keyof T] ?? "")}
                      </td>
                    ))}
                  </tr>
                  {renderExpanded && isOpen ? (
                    <tr>
                      <td colSpan={columns.length + 1} className="bg-paper/60 px-5 py-4">
                        {renderExpanded(row)}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > pageSize ? (
        <div className="flex flex-col gap-3 border-t border-line bg-paper/60 px-4 py-3 text-sm text-ink/65 md:flex-row md:items-center md:justify-between">
          <p>
            Mostrando {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, rows.length)} de {rows.length} registros
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={page === 1}
              className="focus-ring rounded-[8px] border border-line bg-white px-3 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            >
              Anterior
            </button>
            <span className="px-2 font-medium text-ink">
              Pagina {page} de {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              disabled={page === totalPages}
              className="focus-ring rounded-[8px] border border-line bg-white px-3 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            >
              Siguiente
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
