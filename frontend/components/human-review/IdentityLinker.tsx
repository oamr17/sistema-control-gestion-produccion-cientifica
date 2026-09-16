import type {
  CounterpartOption,
  DuplicatePayload,
  DuplicateResolution,
  ExternalPayload,
  LinkResolution,
  PersonPayload,
  RelationPayload
} from "../../lib/human-review";

export type IdentityLinkPayload = PersonPayload | ExternalPayload | RelationPayload | DuplicatePayload;

const resolutions: { value: LinkResolution; label: string }[] = [
  { value: "linked", label: "Vincular" },
  { value: "maintained_separate", label: "Mantener separado" },
  { value: "separated", label: "Separar" }
];

const duplicateResolutions: { value: DuplicateResolution; label: string }[] = [
  { value: "merged", label: "Fusionar" },
  { value: "maintained_separate", label: "Mantener separado" },
  { value: "separated", label: "Separar" }
];

const inputClass = "focus-ring mt-1 w-full rounded-[8px] border border-line bg-white px-3 py-2 text-sm text-ink disabled:cursor-not-allowed disabled:bg-paper/70";

function RequiredMarker() {
  return <><span aria-hidden="true" className="text-coral"> *</span><span className="sr-only"> obligatorio</span></>;
}

type IdentityLinkField =
  | "canonical_identity_key"
  | "canonical_name"
  | "aliases"
  | "project_director_identity_key"
  | "relationship_status"
  | "external_identity_key"
  | "external_institution"
  | "counterpart_ref"
  | "resolution";

type IdentityLinkFieldErrors = Partial<Record<IdentityLinkField, string>>;

function LinkFieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span id={id} role="alert" className="mt-2 block text-sm font-semibold text-coral">{message}</span> : null;
}

export function IdentityLinker({
  payload,
  counterpartOptions = [],
  disabled,
  fieldErrors = {},
  idPrefix = "identity-link",
  onChange
}: {
  payload: IdentityLinkPayload;
  counterpartOptions?: CounterpartOption[];
  disabled: boolean;
  fieldErrors?: IdentityLinkFieldErrors;
  idPrefix?: string;
  onChange: (payload: IdentityLinkPayload) => void;
}) {
  if (payload.case_type === "possible_duplicate") {
    const selected = `${payload.counterpart_ref.target_type}:${payload.counterpart_ref.target_id}`;
    return (
      <fieldset disabled={disabled} className="space-y-4 rounded-[8px] border border-line bg-paper/45 p-4">
        <legend className="px-1 text-sm font-semibold text-ink">Contraparte del posible duplicado</legend>
        <label className="block text-sm font-medium text-ink/75">
          Seleccionar contraparte<RequiredMarker />
          <select
            value={selected}
            aria-required="true"
            aria-invalid={Boolean(fieldErrors.counterpart_ref)}
            aria-describedby={fieldErrors.counterpart_ref ? `${idPrefix}-counterpart-error` : undefined}
            onChange={(event) => {
              const option = counterpartOptions.find(({ counterpart_ref: reference }) =>
                `${reference.target_type}:${reference.target_id}` === event.target.value
              );
              if (option) onChange({ ...payload, counterpart_ref: { ...option.counterpart_ref } });
            }}
            className={inputClass}
          >
            {counterpartOptions.map((option) => {
              const reference = option.counterpart_ref;
              const value = `${reference.target_type}:${reference.target_id}`;
              const context = [option.source_label, option.document_name].filter(Boolean).join(" · ");
              return <option key={value} value={value}>{option.display_name}{context ? ` · ${context}` : ""}</option>;
            })}
          </select>
          <LinkFieldError id={`${idPrefix}-counterpart-error`} message={fieldErrors.counterpart_ref} />
          <span className="mt-1 block text-xs font-normal text-ink/50">Solo se muestran coincidencias vigentes entregadas para este caso.</span>
        </label>
        <label className="block text-sm font-medium text-ink/75">
          Resolución<RequiredMarker />
          <select value={payload.resolution} aria-required="true" aria-invalid={Boolean(fieldErrors.resolution)} aria-describedby={fieldErrors.resolution ? `${idPrefix}-resolution-error` : undefined} onChange={(event) => onChange({ ...payload, resolution: event.target.value as DuplicateResolution })} className={inputClass}>
            {duplicateResolutions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <LinkFieldError id={`${idPrefix}-resolution-error`} message={fieldErrors.resolution} />
        </label>
      </fieldset>
    );
  }

  const reference = payload.case_type === "external_identity"
    ? payload.external_identity_key
    : payload.case_type === "project_director_relation"
      ? payload.project_director_identity_key ?? ""
      : payload.canonical_identity_key ?? "";
  const resolution = payload.case_type === "project_director_relation"
    ? payload.relationship_status
    : payload.resolution ?? "linked";
  const referenceField = payload.case_type === "external_identity"
    ? "external_identity_key"
    : payload.case_type === "project_director_relation"
      ? "project_director_identity_key"
      : "canonical_identity_key";
  const referenceError = fieldErrors[referenceField];
  const resolutionField = payload.case_type === "project_director_relation" ? "relationship_status" : "resolution";
  const resolutionError = fieldErrors[resolutionField];
  const autoAssignsIdentityReference = payload.case_type === "person_identity" || payload.case_type === "author_identity" || payload.case_type === "external_identity";

  const setReference = (value: string) => {
    if (payload.case_type === "external_identity") onChange({ ...payload, external_identity_key: value });
    else if (payload.case_type === "project_director_relation") onChange({ ...payload, project_director_identity_key: value || null });
    else onChange({ ...payload, canonical_identity_key: value });
  };

  const setResolution = (value: LinkResolution) => {
    if (payload.case_type === "project_director_relation") onChange({ ...payload, relationship_status: value });
    else onChange({ ...payload, resolution: value });
  };

  return (
    <fieldset disabled={disabled} className="space-y-4 rounded-[8px] border border-line bg-paper/45 p-4">
      <legend className="px-1 text-sm font-semibold text-ink">Entidad seleccionada</legend>
      {autoAssignsIdentityReference ? (
        <div className="rounded-[8px] border border-line bg-white px-4 py-3 text-sm text-ink/70">
          <p className="font-semibold text-ink/75">Identificador canónico público</p>
          <p className="mt-1">Se asignará automáticamente al confirmar.</p>
          <p className="mt-1 text-xs text-ink/50">No es necesario ingresar rutas ni claves técnicas.</p>
          <LinkFieldError id={`${idPrefix}-reference-error`} message={referenceError} />
        </div>
      ) : (
        <label className="block text-sm font-medium text-ink/75">
          Identificador canónico público
          <input
            value={reference ?? ""}
            aria-invalid={Boolean(referenceError)}
            aria-describedby={referenceError ? `${idPrefix}-reference-error` : undefined}
            onChange={(event) => setReference(event.target.value)}
            maxLength={320}
            autoComplete="off"
            spellCheck={false}
            placeholder="Ej. identity:persona-publica"
            className={inputClass}
          />
          <LinkFieldError id={`${idPrefix}-reference-error`} message={referenceError} />
          <span className="mt-1 block text-xs font-normal text-ink/50">Usa solamente el identificador público autorizado; no ingreses rutas ni claves técnicas.</span>
        </label>
      )}
      {(payload.case_type === "person_identity" || payload.case_type === "author_identity") ? (
        <label className="block text-sm font-medium text-ink/75">
          Nombre canónico<RequiredMarker />
          <input value={payload.canonical_name} aria-required="true" aria-invalid={Boolean(fieldErrors.canonical_name)} aria-describedby={fieldErrors.canonical_name ? `${idPrefix}-canonical-name-error` : undefined} onChange={(event) => onChange({ ...payload, canonical_name: event.target.value })} maxLength={500} className={inputClass} />
          <LinkFieldError id={`${idPrefix}-canonical-name-error`} message={fieldErrors.canonical_name} />
        </label>
      ) : null}
      {(payload.case_type === "person_identity" || payload.case_type === "author_identity") ? (
        <label className="block text-sm font-medium text-ink/75">
          Alias, separados por coma
          <input value={(payload.aliases ?? []).join(",")} aria-invalid={Boolean(fieldErrors.aliases)} aria-describedby={fieldErrors.aliases ? `${idPrefix}-aliases-error` : undefined} onChange={(event) => onChange({ ...payload, aliases: event.target.value.split(",") })} placeholder="Ej. Ana María, María José" className={inputClass} />
          <span className="mt-1 block text-xs font-normal text-ink/50">Opcional. Separa los alias con comas; puedes incluir espacios dentro de cada nombre.</span>
          <LinkFieldError id={`${idPrefix}-aliases-error`} message={fieldErrors.aliases} />
        </label>
      ) : null}
      {payload.case_type === "external_identity" ? (
        <label className="block text-sm font-medium text-ink/75">
          Institución externa
          <input value={payload.external_institution ?? ""} aria-invalid={Boolean(fieldErrors.external_institution)} aria-describedby={fieldErrors.external_institution ? `${idPrefix}-external-institution-error` : undefined} onChange={(event) => onChange({ ...payload, external_institution: event.target.value || null })} maxLength={500} className={inputClass} />
          <LinkFieldError id={`${idPrefix}-external-institution-error`} message={fieldErrors.external_institution} />
        </label>
      ) : null}
      <label className="block text-sm font-medium text-ink/75">
        Resolución<RequiredMarker />
        <select value={resolution} aria-required="true" aria-invalid={Boolean(resolutionError)} aria-describedby={resolutionError ? `${idPrefix}-resolution-error` : undefined} onChange={(event) => setResolution(event.target.value as LinkResolution)} className={inputClass}>
          {resolutions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <LinkFieldError id={`${idPrefix}-resolution-error`} message={resolutionError} />
      </label>
    </fieldset>
  );
}
