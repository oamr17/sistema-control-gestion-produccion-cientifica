import { useId } from "react";

import {
  MAX_DECISION_REASON_LENGTH,
  buildDecisionPreview,
  type CaseDetail,
  type DecisionDraft,
  type ReviewCaseType
} from "../../lib/human-review";
import {
  DecisionPayloadFields,
  type DecisionPayloadFieldErrors
} from "./DecisionComposer";
import { DecisionPreview } from "./DecisionPreview";
import { safePublicText } from "./DetectedDataPanel";

type GuidedCaseType = Extract<
  ReviewCaseType,
  "person_identity" | "author_identity" | "product" | "project_director_relation" | "external_identity" | "possible_duplicate"
>;

const guidanceByCaseType: Record<GuidedCaseType, { verify: string; registered: string; reason: string }> = {
  person_identity: {
    verify: "Confirma si los nombres corresponden a la misma persona y revisa la forma canónica.",
    registered: "Quedarán registrados el nombre canónico y sus alias públicos.",
    reason: "Explica la evidencia utilizada para validar la identidad de la persona."
  },
  author_identity: {
    verify: "Comprueba la autoría y que las variantes del nombre pertenecen al mismo autor.",
    registered: "Quedarán registrados el nombre canónico del autor y sus alias públicos.",
    reason: "Explica qué evidencia permite confirmar la autoría."
  },
  product: {
    verify: "Revisa que el título represente correctamente el producto detectado.",
    registered: "Quedará registrado el título validado del producto.",
    reason: "Explica por qué el título propuesto describe mejor el producto."
  },
  project_director_relation: {
    verify: "Confirma la identidad del director y el estado correcto de la relación con el proyecto.",
    registered: "Quedará registrada la relación validada con el director.",
    reason: "Explica la evidencia que respalda esta relación."
  },
  external_identity: {
    verify: "Verifica la identidad externa y la institución con la que está vinculada.",
    registered: "Quedarán registradas la identidad pública y la institución externa validadas.",
    reason: "Explica cómo se comprobó la identidad en la fuente externa."
  },
  possible_duplicate: {
    verify: "Decide si los registros son duplicados, deben fusionarse o mantenerse separados.",
    registered: "Quedará registrada la relación seleccionada entre los casos comparados.",
    reason: "Explica las señales que justifican vincular o mantener separados los registros."
  }
};

function guidanceFor(caseType: ReviewCaseType) {
  if (caseType === "invalid_text" || caseType === "new_evidence_conflict") return null;
  return guidanceByCaseType[caseType];
}

function presentValidation(draft: DecisionDraft, messages: readonly string[]) {
  const fieldErrors: DecisionPayloadFieldErrors = {};
  const generalErrors: string[] = [];
  let reasonError: string | undefined;

  for (const message of messages) {
    if (message.startsWith("El motivo ")) reasonError ??= message;
    else if (message.includes("nombre canónico")) fieldErrors.canonical_name ??= message;
    else if (message.includes("Los alias")) fieldErrors.aliases ??= message;
    else if (message.includes("El título")) fieldErrors.product_title ??= message;
    else if (message.includes("La institución")) fieldErrors.external_institution ??= message;
    else if (message.includes("contraparte pública")) fieldErrors.counterpart_ref ??= message;
    else if (message.includes("resolución de vínculo")) {
      if (draft.payload.case_type === "project_director_relation") fieldErrors.relationship_status ??= message;
      else fieldErrors.resolution ??= message;
    }
    else if (message.includes("identificador canónico")) {
      if (draft.payload.case_type === "project_director_relation") fieldErrors.project_director_identity_key ??= message;
      else if (draft.payload.case_type === "external_identity") fieldErrors.external_identity_key ??= message;
      else fieldErrors.canonical_identity_key ??= message;
    } else generalErrors.push(message);
  }
  return { fieldErrors, generalErrors, reasonError };
}

type GuidedDecisionEditorProps = {
  detail: CaseDetail;
  draft: DecisionDraft;
  disabled: boolean;
  previewOpen: boolean;
  validationMessages: readonly string[];
  onChange: (draft: DecisionDraft) => void;
  onPreviewOpenChange: (open: boolean) => void;
};

export function GuidedDecisionEditor({
  detail,
  draft,
  disabled,
  previewOpen,
  validationMessages,
  onChange,
  onPreviewOpenChange
}: GuidedDecisionEditorProps) {
  const instanceId = useId();
  const guidance = guidanceFor(detail.case_type);
  if (!guidance) {
    return <p role="alert" className="rounded-[8px] border border-coral/30 p-4 text-sm text-coral">Este tipo de revisión no admite una decisión guiada.</p>;
  }
  const { fieldErrors, generalErrors, reasonError } = presentValidation(draft, validationMessages);
  const reasonRequired = draft.action !== "approve" || buildDecisionPreview(detail, draft).changedFields.length > 0;
  const idPrefix = `guided-${instanceId}`;
  const detectedHeadingId = `${idPrefix}-detected-heading`;
  const verifyHeadingId = `${idPrefix}-verify-heading`;
  const registeredHeadingId = `${idPrefix}-registered-heading`;
  const reasonHeadingId = `${idPrefix}-reason-heading`;
  const reasonLabelId = `${idPrefix}-reason-label`;
  const reasonInputId = `${idPrefix}-reason-input`;
  const reasonGuidanceId = `${idPrefix}-reason-guidance`;
  const reasonErrorId = `${idPrefix}-reason-error`;
  const describedBy = reasonError
    ? `${reasonGuidanceId} ${reasonErrorId}`
    : reasonGuidanceId;

  return (
    <section aria-label="Editor guiado de decisión" className="space-y-5">
      <section aria-labelledby={detectedHeadingId} className="rounded-[8px] border border-line bg-white p-4">
        <h3 id={detectedHeadingId} className="font-semibold text-ink">Qué detectó el sistema</h3>
        <p className="mt-2 break-words text-sm text-ink/70">{safePublicText(detail.detected_value)}</p>
      </section>

      <section aria-labelledby={verifyHeadingId} className="rounded-[8px] border border-line bg-white p-4">
        <h3 id={verifyHeadingId} className="font-semibold text-ink">Qué debes verificar</h3>
        <p className="mt-2 text-sm leading-6 text-ink/70">{guidance.verify}</p>
      </section>

      <section aria-labelledby={registeredHeadingId} className="rounded-[8px] border border-line bg-white p-4">
        <h3 id={registeredHeadingId} className="font-semibold text-ink">Qué dato quedará registrado</h3>
        <p className="mt-2 text-sm leading-6 text-ink/70">{guidance.registered}</p>
        <p className="mt-3 text-xs font-semibold text-ink/60"><span aria-hidden="true" className="text-coral">*</span> Campo obligatorio</p>
        <div className="mt-4">
          <DecisionPayloadFields
            detail={detail}
            draft={draft}
            disabled={disabled}
            fieldErrors={fieldErrors}
            idPrefix={`${idPrefix}-payload`}
            onChange={(payload) => onChange({ ...draft, payload })}
          />
          {generalErrors.map((message) => <p key={message} role="alert" className="mt-2 text-sm font-semibold text-coral">{message}</p>)}
        </div>
      </section>

      <section aria-labelledby={reasonHeadingId} className="rounded-[8px] border border-line bg-white p-4">
        <h3 id={reasonHeadingId} className="font-semibold text-ink">Por qué se realiza el cambio</h3>
        <label id={reasonLabelId} htmlFor={reasonInputId} className="mt-3 block text-sm font-semibold text-ink">{reasonRequired ? <><span>Motivo obligatorio</span><span aria-hidden="true" className="text-coral"> *</span><span className="sr-only"> obligatorio</span></> : "Motivo cuando exista un cambio"}</label>
        <p id={reasonGuidanceId} className="mt-2 text-sm leading-6 text-ink/70">{guidance.reason}</p>
        <textarea
          id={reasonInputId}
          value={draft.reason}
          disabled={disabled}
          aria-required={reasonRequired || undefined}
          maxLength={MAX_DECISION_REASON_LENGTH}
          rows={3}
          aria-invalid={Boolean(reasonError)}
          aria-labelledby={reasonLabelId}
          aria-describedby={describedBy}
          onChange={(event) => onChange({ ...draft, reason: event.target.value })}
          className="focus-ring mt-3 w-full resize-y rounded-[8px] border border-line bg-white px-3 py-2 text-sm text-ink disabled:cursor-not-allowed disabled:bg-paper/70"
        />
        {reasonError ? <p id={reasonErrorId} role="alert" className="mt-2 text-sm font-semibold text-coral">{reasonError}</p> : null}
      </section>

      <button
        type="button"
        disabled={disabled}
        aria-expanded={previewOpen}
        onClick={() => onPreviewOpenChange(!previewOpen)}
        className="focus-ring rounded-[8px] border border-line px-4 py-2 text-sm font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-50"
      >
        {previewOpen ? "Ocultar vista previa" : "Mostrar vista previa"}
      </button>
      {previewOpen ? <DecisionPreview detail={detail} draft={draft} /> : null}
    </section>
  );
}
