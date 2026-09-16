import { useEffect, useRef, type KeyboardEvent } from "react";
import { RotateCcw } from "lucide-react";

import { MAX_DECISION_REASON_LENGTH } from "../../lib/human-review";

export function RevertConfirmDialog({
  open,
  reason,
  pending,
  confirmationBlocked = false,
  error,
  onReasonChange,
  onCancel,
  onConfirm
}: {
  open: boolean;
  reason: string;
  pending: boolean;
  confirmationBlocked?: boolean;
  error?: string | null;
  onReasonChange: (reason: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const initialFocusRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => initialFocusRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      previous?.focus();
    };
  }, [open]);

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !pending) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("textarea, button:not([disabled])") ?? []);
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

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/35 p-4 sm:items-center" role="presentation">
      <section ref={dialogRef} onKeyDown={handleKeyDown} role="dialog" aria-modal="true" aria-labelledby="revert-dialog-title" aria-describedby="revert-dialog-description" className="w-full max-w-lg rounded-[8px] border border-line bg-white p-6 shadow-soft">
        <div className="flex items-start gap-3">
          <span className="rounded-full bg-grape/30 p-2 text-ink"><RotateCcw size={18} aria-hidden="true" /></span>
          <div>
            <h3 id="revert-dialog-title" className="text-lg font-semibold text-ink">Confirmar reversión</h3>
            <p id="revert-dialog-description" className="mt-1 text-sm leading-6 text-ink/60">La decisión vigente se revertirá de forma auditable solo después de confirmar.</p>
          </div>
        </div>
        <label className="mt-5 block text-sm font-medium text-ink/75">
          Motivo obligatorio<span aria-hidden="true" className="text-coral"> *</span><span className="sr-only"> obligatorio</span>
          <textarea ref={initialFocusRef} value={reason} onChange={(event) => onReasonChange(event.target.value)} aria-required="true" maxLength={MAX_DECISION_REASON_LENGTH} rows={4} disabled={pending} className="focus-ring mt-1 w-full resize-y rounded-[8px] border border-line px-3 py-2 text-sm text-ink disabled:bg-paper/70" />
        </label>
        <p className="mt-1 text-right text-xs text-ink/45">{reason.length}/{MAX_DECISION_REASON_LENGTH}</p>
        {error ? <p role="alert" className="mt-3 text-sm font-semibold text-coral">{error}</p> : null}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onCancel} disabled={pending} className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink disabled:opacity-50">Cancelar</button>
          <button type="button" onClick={onConfirm} disabled={pending || confirmationBlocked || !reason.trim()} className="focus-ring rounded-[8px] bg-ink px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{pending ? "Confirmando..." : "Revertir decisión"}</button>
        </div>
      </section>
    </div>
  );
}
