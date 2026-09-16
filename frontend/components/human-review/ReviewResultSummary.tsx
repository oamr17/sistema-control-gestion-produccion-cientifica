import type { ReviewCommandResult } from "../../lib/human-review";
import { safePublicText } from "./DetectedDataPanel";

const resultLabels: Record<ReviewCommandResult["status"], string> = {
  confirmed: "Revisión confirmada",
  conflict: "Revisión requiere atención",
  forbidden: "Sin permiso para confirmar",
  invalid: "Revisión con datos inválidos",
  unavailable: "Servicio temporalmente no disponible",
  not_found: "Revisión no disponible"
};

export function ReviewResultSummary({ result }: { result: ReviewCommandResult }) {
  const message = safePublicText(result.message, "No hay detalles públicos disponibles.");
  const reference = safePublicText(result.correlationId, "");

  if (result.status !== "confirmed") {
    return (
      <div role="alert" className="rounded-[8px] border border-coral/30 bg-coral/5 p-3 text-sm text-ink">
        <p className="font-semibold text-coral">{resultLabels[result.status]}</p>
        <p className="mt-2 text-ink/70">{message}</p>
        {reference ? <p className="mt-2 text-xs text-ink/55">Referencia de soporte: {reference}</p> : null}
      </div>
    );
  }

  return (
    <details className="rounded-[8px] border border-line bg-paper/55 p-3 text-sm">
      <summary className="cursor-pointer font-semibold text-ink">{resultLabels[result.status]}</summary>
      <p className="mt-2 text-ink/70">{message}</p>
      {reference ? <p className="mt-2 text-xs text-ink/55">Referencia de soporte: {reference}</p> : null}
    </details>
  );
}
