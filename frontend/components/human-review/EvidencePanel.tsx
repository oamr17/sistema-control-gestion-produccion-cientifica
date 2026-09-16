"use client";

import { useEffect, useRef, useState } from "react";
import { ExternalLink, FileSearch } from "lucide-react";

import type { EvidenceSummary, PublicScalar } from "../../lib/human-review";
import type { HumanReviewApiError } from "../../lib/human-review-api";
import { safePublicText } from "./DetectedDataPanel";

type ObjectUrlApi = {
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
};

export function replaceEvidenceObjectUrl(api: ObjectUrlApi, previousUrl: string | null, blob: Blob): string {
  if (previousUrl) api.revokeObjectURL(previousUrl);
  return api.createObjectURL(blob);
}

export function revokeEvidenceObjectUrl(api: ObjectUrlApi, objectUrl: string | null): void {
  if (objectUrl) api.revokeObjectURL(objectUrl);
}

function isEvidenceAvailable(value: PublicScalar | undefined, count: PublicScalar | undefined): boolean {
  return value === true || value === "true" || (typeof count === "number" && count > 0);
}

function publicPage(value: PublicScalar | undefined): string | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? String(value) : null;
}

function evidenceErrorMessage(error: HumanReviewApiError): string {
  return error.status === 503
    ? "La evidencia no est\u00e1 disponible temporalmente."
    : error.status === 403
      ? "No tienes acceso a esta evidencia."
      : error.status === 404
        ? "La evidencia solicitada no existe."
        : "No pudimos cargar la evidencia.";
}

export function EvidencePanel({
  summary,
  blob,
  objectUrl: suppliedObjectUrl,
  documentObjectUrl,
  error,
  documentError,
  isLoading,
  isDocumentLoading = false,
  onLoad,
  onLoadDocument
}: {
  summary: EvidenceSummary;
  blob?: Blob | null;
  objectUrl?: string | null;
  documentObjectUrl?: string | null;
  error: HumanReviewApiError | null;
  documentError?: HumanReviewApiError | null;
  isLoading: boolean;
  isDocumentLoading?: boolean;
  onLoad: () => void;
  onLoadDocument?: () => void;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const available = isEvidenceAvailable(summary.available, summary.count);
  const identifiedDocumentName = safePublicText(summary.document_name, "");
  const documentName = safePublicText(summary.document_name, "evidence.pdf");
  const page = publicPage(summary.page);
  const section = safePublicText(summary.section, "");
  const fragment = safePublicText(summary.fragment);

  useEffect(() => {
    if (suppliedObjectUrl !== undefined) {
      revokeEvidenceObjectUrl(URL, objectUrlRef.current);
      objectUrlRef.current = null;
      setObjectUrl(null);
      return;
    }
    if (!blob) {
      revokeEvidenceObjectUrl(URL, objectUrlRef.current);
      objectUrlRef.current = null;
      setObjectUrl(null);
      return;
    }
    const nextUrl = replaceEvidenceObjectUrl(URL, objectUrlRef.current, blob);
    objectUrlRef.current = nextUrl;
    setObjectUrl(nextUrl);
  }, [blob, suppliedObjectUrl]);

  useEffect(() => () => {
    revokeEvidenceObjectUrl(URL, objectUrlRef.current);
    objectUrlRef.current = null;
  }, []);

  const localUrl = suppliedObjectUrl ?? objectUrl ?? documentObjectUrl ?? null;
  const previewUrl = localUrl && page ? `${localUrl}#page=${page}` : localUrl;
  const renderedError = error ?? documentError ?? null;

  return (
    <section aria-labelledby="evidence-heading" className="rounded-[8px] border border-line bg-white p-5 shadow-soft">
      <div className="flex items-start gap-3">
        <span className="rounded-[8px] bg-mint/20 p-2 text-ink"><FileSearch size={20} aria-hidden="true" /></span>
        <div>
          <h3 id="evidence-heading" className="text-lg font-semibold text-ink">Evidencia documental</h3>
          <p className="mt-1 text-sm text-ink/55">Consulta autenticada de la evidencia asociada al caso.</p>
        </div>
      </div>

      {!available ? (
        <div className="mt-5 space-y-2 text-sm text-ink/60">
          {identifiedDocumentName ? <p>Documento identificado: <span className="font-semibold text-ink">{identifiedDocumentName}</span></p> : null}
          {renderedError ? (
            <div role="alert" className="rounded-[8px] border border-coral/30 p-3 text-sm">
              <p className="font-semibold text-coral">{evidenceErrorMessage(renderedError)}</p>
              {renderedError.correlation_id ? <p className="mt-1 text-ink/55">Referencia de soporte: {safePublicText(renderedError.correlation_id, "")}</p> : null}
            </div>
          ) : null}
          <p>La ubicación precisa no pudo verificarse como evidencia del dato. Puedes consultar el PDF importado asociado al caso.</p>
          <div className="flex flex-wrap gap-2">
            {!localUrl && onLoadDocument ? (
              <button type="button" onClick={onLoadDocument} disabled={isDocumentLoading} className="focus-ring inline-flex items-center gap-2 rounded-[8px] bg-mint px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
                <FileSearch size={17} aria-hidden="true" />
                {isDocumentLoading ? "Cargando PDF del documento..." : "Abrir PDF del documento"}
              </button>
            ) : null}
            {localUrl ? (
              <a href={localUrl} target="_blank" rel="noopener noreferrer" className="focus-ring inline-flex items-center gap-2 rounded-[8px] border border-line bg-white px-4 py-2 text-sm font-semibold text-ink">
                <ExternalLink size={17} aria-hidden="true" /> Abrir PDF del documento en una pestaña nueva
              </a>
            ) : null}
          </div>
          {previewUrl ? (
            <iframe
              title={`Vista previa de ${documentName}`}
              src={previewUrl}
              className="h-[36rem] w-full rounded-[8px] border border-line bg-paper"
            />
          ) : null}
          <p>El visor de evidencia validada no está disponible para este dato.</p>
        </div>
      ) : (
        <div className="mt-5 space-y-4">
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="text-ink/50">Documento</dt><dd className="mt-1 break-words font-semibold text-ink">{documentName}</dd></div>
            <div><dt className="text-ink/50">{"P\u00e1gina"}</dt><dd className="mt-1 font-semibold text-ink">{page ? `P\u00e1gina ${page}` : "No disponible"}</dd></div>
            <div><dt className="text-ink/50">{"Secci\u00f3n"}</dt><dd className="mt-1 break-words font-semibold text-ink">{section || "No disponible"}</dd></div>
          </dl>
          <div className="rounded-[8px] border-l-4 border-mint bg-paper/65 p-4 text-sm leading-6 text-ink/75">
            <p className="font-semibold text-ink">Fragmento</p>
            <p className="mt-1">{fragment}</p>
          </div>
          {renderedError ? (
            <div role="alert" className="rounded-[8px] border border-coral/30 p-3 text-sm">
              <p className="font-semibold text-coral">{evidenceErrorMessage(renderedError)}</p>
              {renderedError.correlation_id ? <p className="mt-1 text-ink/55">Referencia de soporte: {safePublicText(renderedError.correlation_id, "")}</p> : null}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={onLoad} disabled={isLoading} className="focus-ring inline-flex items-center gap-2 rounded-[8px] bg-mint px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
              <FileSearch size={17} aria-hidden="true" />
              {isLoading ? "Cargando evidencia..." : localUrl ? "Actualizar evidencia" : "Cargar evidencia"}
            </button>
            {localUrl ? (
              <a href={localUrl} target="_blank" rel="noopener noreferrer" className="focus-ring inline-flex items-center gap-2 rounded-[8px] border border-line bg-white px-4 py-2 text-sm font-semibold text-ink">
                <ExternalLink size={17} aria-hidden="true" /> Abrir documento original en una pestaña nueva
              </a>
            ) : null}
          </div>
          {previewUrl ? (
            <iframe
              title={`Vista previa de ${documentName}`}
              src={previewUrl}
              className="h-[36rem] w-full rounded-[8px] border border-line bg-paper"
            />
          ) : null}
        </div>
      )}
    </section>
  );
}
