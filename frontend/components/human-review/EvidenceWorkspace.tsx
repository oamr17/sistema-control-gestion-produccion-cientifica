"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { EvidenceSummary } from "../../lib/human-review";
import { ApiFetchError, api } from "../../lib/api";
import { useActiveHumanReviewEvidence } from "../../hooks/useHumanReview";
import { HumanReviewApiError } from "../../lib/human-review-api";
import { EvidencePanel } from "./EvidencePanel";

type OwnedObjectUrl = {
  caseId: string;
  blob: Blob;
  url: string;
};

type ImportedDocumentState = {
  caseId: string | null;
  documentId: number | null;
  blob: Blob | null;
  error: HumanReviewApiError | null;
  isLoading: boolean;
};

export function EvidenceWorkspace({
  caseId,
  documentId,
  evidenceSummary,
  responsiveSession = false
}: {
  caseId: string | null;
  documentId?: number | null;
  evidenceSummary: EvidenceSummary;
  responsiveSession?: boolean;
}) {
  const evidence = useActiveHumanReviewEvidence(caseId);
  const [ownedObjectUrl, setOwnedObjectUrl] = useState<OwnedObjectUrl | null>(null);
  const [importedDocument, setImportedDocument] = useState<ImportedDocumentState>({
    caseId: null, documentId: null, blob: null, error: null, isLoading: false
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const objectUrlRef = useRef<OwnedObjectUrl | null>(null);
  const documentRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    documentRequest.current?.abort();
    documentRequest.current = null;
    setImportedDocument({ caseId, documentId: documentId ?? null, blob: null, error: null, isLoading: false });
  }, [caseId, documentId]);

  const loadImportedDocument = useCallback(() => {
    if (!caseId || !documentId) return;
    documentRequest.current?.abort();
    const controller = new AbortController();
    documentRequest.current = controller;
    setImportedDocument({ caseId, documentId, blob: null, error: null, isLoading: true });
    void api.importJobFile(documentId, { signal: controller.signal })
      .then((blob) => {
        if (!controller.signal.aborted) setImportedDocument({ caseId, documentId, blob, error: null, isLoading: false });
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        const error = caught instanceof ApiFetchError
          ? new HumanReviewApiError(caught)
          : new HumanReviewApiError(new ApiFetchError("Request failed", { status: 0 }));
        setImportedDocument({ caseId, documentId, blob: null, error, isLoading: false });
      });
  }, [caseId, documentId]);

  const importedBlob = importedDocument.caseId === caseId && importedDocument.documentId === (documentId ?? null)
    ? importedDocument.blob
    : null;
  const previewBlob = evidence.blob ?? importedBlob;

  useEffect(() => {
    if (!caseId || !previewBlob) {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current.url);
      objectUrlRef.current = null;
      setOwnedObjectUrl(null);
      return;
    }
    const current = objectUrlRef.current;
    if (current?.caseId === caseId && current.blob === previewBlob) return;
    if (current) URL.revokeObjectURL(current.url);
    const next = { caseId, blob: previewBlob, url: URL.createObjectURL(previewBlob) };
    objectUrlRef.current = next;
    setOwnedObjectUrl(next);
  }, [caseId, previewBlob]);

  useEffect(() => () => {
    documentRequest.current?.abort();
    documentRequest.current = null;
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current.url);
    objectUrlRef.current = null;
  }, []);

  const objectUrl = ownedObjectUrl?.caseId === caseId && ownedObjectUrl.blob === previewBlob
    ? ownedObjectUrl.url
    : null;

  const panel = (
    <EvidencePanel
      summary={evidenceSummary}
      objectUrl={objectUrl}
      error={evidence.error}
      documentError={importedDocument.error}
      isLoading={evidence.isLoading}
      isDocumentLoading={importedDocument.isLoading}
      onLoad={evidence.reload}
      onLoadDocument={documentId ? loadImportedDocument : undefined}
    />
  );

  if (responsiveSession) {
    return (
      <section aria-label="Evidencia de la revisión activa" className="rounded-[8px] border border-line bg-white p-4 md:border-0 md:bg-transparent md:p-0">
        <button
          type="button"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((current) => !current)}
          className="focus-ring w-full rounded-[8px] text-left font-semibold text-ink md:hidden"
        >
          {mobileOpen ? "Ocultar evidencia de esta revisión" : "Ver evidencia de esta revisión"}
        </button>
        <div className={`${mobileOpen ? "block" : "hidden"} mt-4 md:mt-0 md:block`}>
          {panel}
        </div>
      </section>
    );
  }

  return (
    <details className="rounded-[8px] border border-line bg-white p-4">
      <summary className="cursor-pointer font-semibold text-ink">Ver evidencia de esta revisión</summary>
      <div className="mt-4">{panel}</div>
    </details>
  );
}
