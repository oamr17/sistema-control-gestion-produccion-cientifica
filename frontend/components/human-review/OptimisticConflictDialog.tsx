"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import type { HumanReviewApiError } from "../../lib/human-review-api";
import { safePublicText } from "./DetectedDataPanel";

export function OptimisticConflictDialog({
  conflict,
  isReloading,
  reloadError = null,
  onCancel,
  onReload
}: {
  conflict: HumanReviewApiError | null;
  isReloading: boolean;
  reloadError?: HumanReviewApiError | null;
  onCancel: () => void;
  onReload: () => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const reloadRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!conflict) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => reloadRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      previous?.focus();
    };
  }, [conflict]);

  if (!conflict) return null;
  const supportReference = safePublicText(reloadError?.correlation_id || conflict.correlation_id, "");
  const reloadMessage = reloadError
    ? "No pudimos recargar el caso. El borrador y los datos de concurrencia anteriores se conservan."
    : null;
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !isReloading) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/35 p-4 sm:items-center" role="presentation">
      <section ref={dialogRef} onKeyDown={handleKeyDown} role="dialog" aria-modal="true" aria-labelledby="optimistic-conflict-title" aria-describedby="optimistic-conflict-description" className="w-full max-w-lg rounded-[8px] border border-line bg-white p-6 shadow-soft">
        <div className="flex items-start gap-3">
          <span className="rounded-full bg-coral/15 p-2 text-coral"><AlertTriangle size={18} aria-hidden="true" /></span>
          <div>
            <h3 id="optimistic-conflict-title" className="text-lg font-semibold text-ink">El caso fue actualizado</h3>
            <p id="optimistic-conflict-description" className="mt-1 text-sm leading-6 text-ink/60">{"El caso fue actualizado por otra operación. Recarga la información vigente y revisa el borrador antes de confirmar nuevamente."}</p>
          </div>
        </div>
        {reloadMessage ? <p role="alert" className="mt-4 rounded-[8px] border border-coral/30 bg-coral/5 p-3 text-sm font-semibold text-coral">{reloadMessage}</p> : null}
        {supportReference ? <p className="mt-4 text-xs text-ink/55">Referencia de soporte: {supportReference}</p> : null}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onCancel} disabled={isReloading} className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink disabled:opacity-50">Cancelar</button>
          <button ref={reloadRef} type="button" onClick={onReload} disabled={isReloading} className="focus-ring inline-flex items-center justify-center gap-2 rounded-[8px] bg-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"><RefreshCw size={16} aria-hidden="true" />{isReloading ? "Recargando..." : "Recargar caso"}</button>
        </div>
      </section>
    </div>
  );
}
