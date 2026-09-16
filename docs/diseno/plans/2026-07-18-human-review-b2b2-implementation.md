# Human Review B2B.2 Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Implementar la bandeja operativa de revisión humana con comandos transaccionales, auditoría semántica, proyección inmediata, KPI y frontend protegido por capacidades.

**Architecture:** Capa explícita de queries y commands B2B.2 sobre los servicios persistentes B2B.1. Las escrituras usan autorización, CAS, decisión append-only, proyección, auditoría y commit único. El frontend mantiene únicamente previews locales.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL 16, Pydantic, unittest, Next.js 14 App Router, TypeScript, Tailwind y la infraestructura efectiva encontrada en el repositorio.

## Global Constraints

- No ejecutar ninguna tarea hasta su autorización y gate humano aplicable.
- B2B.2 no persiste propuestas, borradores, reservas ni ownership; preview es estado local sin request.
- `RESEARCH_MANAGER` es la única autoridad científica; `SYSTEM_ADMIN` solo consulta fundamentos/auditoría; `CAREER_MANAGER` no accede a B2B.
- Toda escritura usa `expected_version`, `expected_current_decision_id`, `correlation_id`, payload Pydantic `extra="forbid"`, motivo cuando aplica y una transacción/commit único.
- Decisiones y auditoría son append-only; una reversión conserva `functional_reversion` y crea `decision_type=reverted`.
- La transición directa `pending → resolved` se añade sin estados nuevos, reservas ni escritura intermedia.
- Los lectores humanos bloqueados prevalecen tras commit; `KpiService` consume esa precedencia inmediatamente.
- No prometer idempotencia HTTP fuerte ni crear tabla de deduplicación; CAS y `correlation_id` son el límite.
- Filtros iniciales: estado, case type, período, documento, source revision, fecha de creación, texto `document_key`/`stable_target_key`; no `career_id`, confianza ni `has_evidence`.
- Evidencia es existente y se entrega mediante streaming autenticado o URL firmada corta, sin `source_path`, bucket key, credenciales o rutas internas.
- No incluir B2B.3–B2B.6, parser, OCR, Dropbox de producto, n8n, exportaciones o administración técnica.
- Baseline: head 0020, 549/549 backend PASS, cero skips, compileall PASS, 79 casos, 80 auditorías, KPI productos elegibles=2, 0021 inexistente.

## File and Interface Map

| Archivo | Acción / responsabilidad | Consume | Produce | Tareas |
|---|---|---|---|---|
| `backend/app/migrations/versions/20260718_0021_human_review_scientific_decision_audit.py` | Extiende check de evento de auditoría; predecessor 0020. | 0020 | `VERSION`, `upgrade`, `downgrade`, `assert_schema` | 1 |
| `backend/app/core/migrations.py` | Registra 0021 una vez autorizada. | módulo 0021 | entrada ordenada `MIGRATIONS` | 1 |
| `backend/app/models/human_review_enums.py` | Enum de evento científico. | vocabulario B2B.1 | `SCIENTIFIC_DECISION_APPLIED` | 1,4 |
| `backend/app/schemas/human_review_operations.py` | Payload audit tipado B2B.2 y comandos existentes. | enums/snapshots | `ScientificDecisionAppliedAuditPayloadV1` | 1,4 |
| `backend/app/services/human_review_audit.py` | Schema/mapping audit. | payload/enums | hash/event válido | 1,4 |
| `backend/app/services/human_review_state.py` | Transición directa autorizada. | `ReviewCaseStatus` | `pending→resolved` | 2,4 |
| `backend/app/schemas/human_review_api.py` | Contratos HTTP cerrados. | schemas B2B.1 | requests/responses/errores | 2,3,8 |
| `backend/app/api/dependencies.py` | Dependencia B2B deny-by-default. | JWT/autorización | `require_b2b_action` | 2,8 |
| `backend/app/services/human_review_queries.py` | Cola, detalle, audit, evidencia metadata. | ORM B2B.1/fuentes | queries tipadas | 3,7,8 |
| `backend/app/services/human_review_commands.py` | Apply/discard y una transacción. | state/projection/audit/auth | `apply_decision`, `discard` | 4,6,8 |
| `backend/app/services/human_review_projection.py` | Reversión ya existente; integración explícita. | snapshots | reversión | 5 |
| `backend/app/services/validated_read_service.py` | Preferencia por proyección humana. | `load_identity_projection`/overrides | lecturas efectivas | 6 |
| `backend/app/services/kpi_service.py` | KPI consume lector efectivo. | `ValidatedReadService` | `KpiEffect` | 6 |
| `backend/app/api/v1/endpoints/human_review.py`, `backend/app/api/v1/router.py` | Router `/human-review`. | services/deps/schemas | OpenAPI/HTTP | 8 |
| `frontend/lib/human-review.ts`, `frontend/lib/human-review-api.ts`, `frontend/lib/data-cache.tsx`, `frontend/hooks/useHumanReview.ts` | tipos, cliente, invalidación y hooks. | contratos API | hooks/cache keys | 9 |
| `frontend/app/human-review/page.tsx`, `frontend/app/human-review/cases/[id]/page.tsx` | bandeja/detalle. | AppShell/API | páginas | 10,11 |
| `frontend/components/human-review/*.tsx` | tabla, filtros, evidencia, preview, comandos, conflicto/timeline. | tipos/hooks | UI | 10–13 |
| `backend/tests/test_human_review_b2b2_*.py`, `frontend/tests/human-review*.test.mjs` | pruebas focales. | interfaces anteriores | RED/GREEN | 1–14 |

## Contracts Produced Before UI

```python
class ReviewQueueQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)
    statuses: tuple[ReviewCaseStatus, ...] = ()
    case_types: tuple[ReviewCaseType, ...] = ()
    period_id: int | None = Field(None, gt=0)
    document_key: str | None = Field(None, min_length=1, max_length=900)
    source_revision: str | None = Field(None, min_length=1, max_length=120)
    created_from: datetime | None = None
    created_to: datetime | None = None
    q: str | None = Field(None, min_length=3, max_length=128)
    sort: Literal["priority_oldest"] = "priority_oldest"

class ApplyDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_current_decision_id: UUID | None = None
    action: Literal["approve", "correct", "link"]
    scope: DecisionScope
    payload: DecisionPayloadV1
    reason: str | None = Field(None, max_length=4000)
    correlation_id: UUID
```

`ReviewQueueResponse`, `ReviewCaseDetail`, `EffectiveCapabilitiesResponse`, `ApplyDecisionResponse`, `DiscardRequest`, `RevertRequest`, `EvidenceResponse`, `AuditTimelineResponse`, `KpiEffect` y `HumanReviewErrorResponse` se definen en Task 2 con el mismo criterio: cada campo procede de `review_items`, una decisión/proyección B2B.1, auditoría o fuente trazable; no hay campos de rutas internas.

```python
class ReviewQueueItem(BaseModel):
    id: UUID; case_type: ReviewCaseType; case_status: ReviewCaseStatus
    scientific_status: ScientificStatus; document_key: str; source_revision: str | None
    source_page: int | None; source_section: str; automatic_priority: int
    manual_priority: int | None; possible_kpi_impact: bool; version: int; created_at: datetime
class ReviewQueueResponse(BaseModel):
    items: tuple[ReviewQueueItem, ...]; total: int; page: int; page_size: int
    facets: dict[str, dict[str, int]]; correlation_id: UUID
class KpiEffectItem(BaseModel): metric: str; before: int; after: int; delta: int
class KpiEffect(BaseModel): affected: tuple[KpiEffectItem, ...]
class ReviewCaseDetail(ReviewQueueItem):
    target_table: ReviewTargetTable; target_pk: int | None; field_path: str
    detected_value: str | None; normalized_value: str | None; canonical_value: str | None
    current_decision_id: UUID | None; overrides: tuple[dict[str, object], ...]
    memberships: tuple[str, ...]; evidence_summary: dict[str, object]
class EffectiveCapabilitiesResponse(BaseModel):
    capability: B2BCapability | None; actions: tuple[B2BAction, ...]
class ApplyDecisionResponse(BaseModel): case: ReviewCaseDetail; decision_id: UUID; kpi_effect: KpiEffect; correlation_id: UUID
class DiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1); expected_current_decision_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=4000); correlation_id: UUID
class RevertRequest(DiscardRequest): decision_id_to_revert: UUID
class EvidenceResponse(BaseModel):
    document_name: str | None; page: int | None; section: str | None; locator: str | None
    fragment: str | None; stream_path: str | None; correlation_id: UUID
class AuditTimelineResponse(BaseModel): items: tuple[dict[str, object], ...]; page: int; page_size: int; total: int; correlation_id: UUID
class HumanReviewErrorResponse(BaseModel): code: str; message: str; correlation_id: UUID; details: dict[str, object] | None = None
```

```ts
export type KpiEffect = { affected: { metric: string; before: number; after: number; delta: number }[] };
export type HumanReviewErrorResponse = { code: string; message: string; correlation_id: string; details?: Record<string, unknown> };
export type ApplyDecisionRequest = { expected_version: number; expected_current_decision_id: string | null; action: "approve"|"correct"|"link"; scope: string; payload: Record<string, unknown>; reason: string | null; correlation_id: string };
export type DiscardRequest = { expected_version: number; expected_current_decision_id: string | null; reason: string; correlation_id: string };
export type RevertRequest = DiscardRequest & { decision_id_to_revert: string };
```

## Dependency Order

### Complete Typed Contract Addendum (authoritative over shorthand above)

`ReviewTargetTable` is the existing enum in `backend/app/models/human_review_enums.py`; Task 2 imports it rather than introducing a new type. The following payload types replace the shorthand `DecisionPayloadV1` and every TypeScript type is emitted verbatim in `frontend/lib/human-review.ts`.

```python
class _Payload(BaseModel): model_config = ConfigDict(extra="forbid")
class PersonDecisionPayload(_Payload): case_type: Literal["person_identity","author_identity"]; canonical_identity_key: str; canonical_name: str; aliases: tuple[str,...] = (); scientific_status: ScientificStatus
class ProductDecisionPayload(_Payload): case_type: Literal["product"]; product_title: str | None = None; scientific_status: ScientificStatus
class RelationDecisionPayload(_Payload): case_type: Literal["project_director_relation"]; project_director_identity_key: str | None = None; relationship_status: str; scientific_status: ScientificStatus
class ExternalDecisionPayload(_Payload): case_type: Literal["external_identity"]; external_identity_key: str; external_institution: str | None = None; scientific_status: ScientificStatus
class DuplicateDecisionPayload(_Payload): case_type: Literal["possible_duplicate"]; counterpart_stable_target_key: str; resolution: Literal["merged","maintained_separate","separated"]; scientific_status: ScientificStatus
DecisionPayloadV1 = PersonDecisionPayload | ProductDecisionPayload | RelationDecisionPayload | ExternalDecisionPayload | DuplicateDecisionPayload
class RevertRequest(_Payload): expected_version: int = Field(ge=1); expected_current_decision_id: UUID; decision_id_to_revert: UUID; reason: str = Field(min_length=1,max_length=4000); correlation_id: UUID
```

```ts
export type QueueQuery={page:number;page_size:number;statuses:string[];case_types:string[];period_id?:number;document_key?:string;source_revision?:string;created_from?:string;created_to?:string;q?:string;sort:"priority_oldest"};
export type QueueItem={id:string;case_type:string;case_status:string;scientific_status:string;document_key:string;source_revision:string|null;source_page:number|null;source_section:string;automatic_priority:number;manual_priority:number|null;possible_kpi_impact:boolean;version:number;created_at:string};
export type QueueResponse={items:QueueItem[];total:number;page:number;page_size:number;facets:Record<string,Record<string,number>>;correlation_id:string};
export type CaseDetail=QueueItem&{target_table:"person_roles"|"scientific_production_authors"|"scientific_productions"|"research_entities"|"external_researchers";target_pk:number|null;field_path:string;detected_value:string|null;normalized_value:string|null;canonical_value:string|null;current_decision_id:string|null;overrides:Record<string,unknown>[];memberships:string[];evidence_summary:Record<string,unknown>};
export type Capabilities={capability:"RESEARCH_MANAGER"|"SYSTEM_ADMIN"|null;actions:string[]}; export type EvidenceResponse={document_name:string|null;page:number|null;section:string|null;locator:string|null;fragment:string|null;stream_path:string|null;correlation_id:string}; export type AuditTimelineResponse={items:Record<string,unknown>[];page:number;page_size:number;total:number;correlation_id:string};
export type PersonPayload={case_type:"person_identity"|"author_identity";canonical_identity_key:string;canonical_name:string;aliases:string[];scientific_status:string}; export type ProductPayload={case_type:"product";product_title?:string;scientific_status:string}; export type RelationPayload={case_type:"project_director_relation";project_director_identity_key?:string;relationship_status:string;scientific_status:string}; export type ExternalPayload={case_type:"external_identity";external_identity_key:string;external_institution?:string;scientific_status:string}; export type DuplicatePayload={case_type:"possible_duplicate";counterpart_stable_target_key:string;resolution:"merged"|"maintained_separate"|"separated";scientific_status:string}; export type DecisionPayload=PersonPayload|ProductPayload|RelationPayload|ExternalPayload|DuplicatePayload;
export type ApplyDecisionRequest={expected_version:number;expected_current_decision_id:string|null;action:"approve"|"correct"|"link";scope:string;payload:DecisionPayload;reason:string|null;correlation_id:string}; export type ApplyDecisionResponse={case:CaseDetail;decision_id:string;kpi_effect:KpiEffect;correlation_id:string};
```

Task 4 exact service signatures are `apply_decision(db: Session, actor: User, review_item_id: UUID, request: ApplyDecisionRequest) -> ApplyDecisionResponse` and `discard_case(db: Session, actor: User, review_item_id: UUID, request: DiscardRequest) -> ApplyDecisionResponse`; Task 8 creates exactly `backend/app/api/v1/endpoints/human_review.py` and registers it only in `backend/app/api/v1/router.py`; frontend files are exactly `ReviewQueueFilters.tsx`, `ReviewQueueTable.tsx`, `ReviewPagination.tsx`, `DetectedDataPanel.tsx`, `EvidencePanel.tsx`, `AuditTimeline.tsx`, `DecisionComposer.tsx`, `DecisionPreview.tsx`, `IdentityLinker.tsx`, `DiscardConfirmDialog.tsx`, `RevertConfirmDialog.tsx`, `OptimisticConflictDialog.tsx` and `CapabilityGate.tsx` under `frontend/components/human-review/`.

The command validator uses this exact derived mapping: `(person_identity|author_identity, approve)->validated`, `(person_identity|author_identity, correct)->corrected`, `(person_identity|author_identity, link)->linked|maintained_separate|separated`; `(product, approve)->validated`, `(product, correct)->corrected`; `(project_director_relation, approve)->validated`, `(project_director_relation, correct)->corrected`, `(project_director_relation, link)->linked|maintained_separate|separated`; `(external_identity, approve)->validated`, `(external_identity, correct)->corrected`, `(external_identity, link)->linked|maintained_separate|separated`; `(possible_duplicate, link)->merged|maintained_separate|separated`. Payloads gain `resolution: Literal["linked","maintained_separate","separated"] | None` for person/external and `relationship_status: Literal["linked","maintained_separate","separated"]` for relation; validation rejects every other pair.

```ts
export type KpiEffect={affected:{metric:string;before:number;after:number;delta:number}[]}; export type HumanReviewErrorResponse={code:string;message:string;correlation_id:string;details?:Record<string,unknown>}; export type DiscardRequest={expected_version:number;expected_current_decision_id:string|null;reason:string;correlation_id:string}; export type RevertRequest={expected_version:number;expected_current_decision_id:string;decision_id_to_revert:string;reason:string;correlation_id:string};
```

`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → (10,11) → 12 → 13 → 14 → 15`. Task 10/11 solo empiezan tras Gate D y Task 15 no está autorizado.

### Task 1: Gate y migración 0021 de auditoría

**Purpose:** Añadir únicamente la taxonomía append-only necesaria para eventos de decisión científica.

**Files:** Create `backend/app/migrations/versions/20260718_0021_human_review_scientific_decision_audit.py`; Modify enums, operations, audit service, migration registry; Test `backend/tests/test_human_review_migration_0021.py`, `test_human_review_audit.py`, `test_human_review_privileges.py`.

**Interfaces:** Consumes `20260713_0020_human_review_audit.AUDIT_EVENT_TYPES`; produces `AuditEventType.SCIENTIFIC_DECISION_APPLIED` and payload `kind="scientific_decision_applied"`.

**Preconditions:** **GATE A: autorización literal antes de crear/modificar cualquier archivo de esta tarea.**

**Stop Gates:** si el catálogo cambia algo distinto del check, hay datos científicos mutados, downgrade no restaura 0020 o permisos app dejan de ser INSERT/SELECT-only sobre audit.

- [ ] RED: crear el test PostgreSQL que afirme que 0020 rechaza `scientific_decision_applied` y que el enum/payload no existe; ejecutar desde `backend`: `$env:B2B1_TEST_DATABASE_URL='<disposable PostgreSQL 16 URL>'; python -m unittest tests.test_human_review_migration_0021 -v`; esperado FAIL focal.
- [ ] GREEN mínimo: crear revisión con `VERSION='20260718_0021_human_review_scientific_decision_audit'`, `down_revision='20260713_0020_human_review_audit'` y SQL exacto `ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_event_type; ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_event_type CHECK (event_type IN ('case_backfilled','locked_decision_imported','identity_created','alias_created','override_created','capability_assigned','capability_revoked','audit_corrected','functional_reversion','scientific_decision_applied'));`; downgrade ejecuta el mismo DROP/ADD pero con exactamente los nueve literales 0020.
- [ ] Añadir enum, payload cerrado `{kind, schema_version, decision_id, decision_type, previous_case_status, resulting_case_status, kpi_effect, review_item_id}`, unión, mapping y `_payload_schema` `audit.scientific_decision_applied.v1`.
- [ ] Ejecutar upgrade→assert catálogo→insert válido→rollback/downgrade→re-upgrade, hash de archivos 0017–0020 intacto y pruebas de append-only/permisos; esperado PASS sin tocar tablas científicas.
- [ ] Generar backup/restore descartable y evidencia; reviewer Spec y Quality; checkpoint. **No aplicar a base real sin GATE B.**

### Task 2: Contratos HTTP, errores y capacidades

**Purpose:** Definir contratos cerrados y autorización HTTP sin rutas aún.

**Files:** Create `backend/app/schemas/human_review_api.py`; Modify `human_review_state.py`, `api/dependencies.py`; Test `backend/tests/test_human_review_b2b2_contracts.py`, `test_human_review_b2b2_authorization.py`.

**Interfaces:** Produces `require_b2b_action(action: B2BAction) -> Callable`, `HumanReviewApiError`, contracts de §Contracts. Consumes `authorize_b2b_action`.

**Stop Gates:** payload abierto, SYSTEM_ADMIN científico, CAREER_MANAGER permitido o action/case incompatible.

- [ ] RED: casos que esperan 403 para CAREER/SYSTEM apply, 422 para payload extra, 400 para motivo ausente y 409 con envelope seguro; ejecutar desde `backend`: `python -m unittest tests.test_human_review_b2b2_contracts tests.test_human_review_b2b2_authorization -v`; esperado FAIL.
- [ ] Implementar la tabla cerrada `(case_type, action) -> decision_type(s)` y derivar `decision_type` solo en servidor; agregar exactamente `(ReviewCaseStatus.PENDING, ReviewCaseStatus.RESOLVED)` al set permitido.
- [ ] Implementar envelope `{"code": code, "message": message, "correlation_id": correlation_id, "details": details}` y dependencia que traduce `B2BAccessDenied` a 403 sin detalle interno.
- [ ] GREEN y regresiones `test_human_review_authorization.py test_human_review_contracts.py`; reviewer/checkpoint.

### Task 3: Query service

**Purpose:** Exponer lectura paginada/detalle/auditoría sin joins o filtros no aprobados.

**Files:** Create `backend/app/services/human_review_queries.py`; Test `backend/tests/test_human_review_b2b2_queries.py`.

**Interfaces:** Produces `HumanReviewQueryService.list_cases(query) -> ReviewQueueResponse`, `get_case(UUID)`, `get_audit(UUID, page, page_size)`.

- [ ] RED: fixtures PostgreSQL que ordenan prioridad/manual/fecha/id, prueban page 1/2, filtros directos y ausencia de `career_id`, `confidence` y `has_evidence`; ejecutar desde `backend`: `python -m unittest tests.test_human_review_b2b2_queries -v`; esperado FAIL.
- [ ] Implementar select de `ReviewItem` con orden `manual_priority DESC NULLS LAST, automatic_priority DESC, created_at ASC, id ASC`, filtros solo de `ReviewQueueQuery`, conteo y facets por estados/case type.
- [ ] Implementar detalle desde target tipado y proyección/auditoría sin exponer raw path; ejecutar EXPLAIN en PostgreSQL de cola filtrada y registrar uso de índices existentes.
- [ ] GREEN/regresiones B2B.1/reviewer/checkpoint.

### Task 4: Command service

**Purpose:** Aplicar approve/correct/link/discard en una transacción atómica.

**Files:** Create `backend/app/services/human_review_commands.py`; Test `backend/tests/test_human_review_b2b2_commands.py`, `test_human_review_b2b2_concurrency.py`.

**Interfaces:** Produces `apply_decision(db: Session, actor: User, review_item_id: UUID, request: ApplyDecisionRequest) -> ApplyDecisionResponse`, `discard_case(db: Session, actor: User, review_item_id: UUID, request: DiscardRequest) -> ApplyDecisionResponse`.

**Required order:** actor→capacidad→item→matriz→version/current decision→payload/reason→`projection_before`→`projection_after`→`kpi_effect`→decision→CAS→materialize projection→audit→flush/checks→single commit→serialize effective view.

- [ ] RED: concurrent commands misma versión: exactamente una decisión/evento y una versión incrementada; rollback al fallar auditoría deja cero filas nuevas.
- [ ] Construir `ReviewDecision` aprobada/locked, usar `set_current_decision`, `append_audit_event_at_current_head`; ningún subordinado llama `commit()`.
- [ ] Crear `scientific_decision_applied` tras materialización, con `kpi_effect` calculado dentro de transacción; mapear `OptimisticLockError` a 409.
- [ ] Ejecutar PostgreSQL 16 concurrente, rollback, append-only, permisos y related regression; reviewer/checkpoint.

### Task 5: Reversión funcional

**Purpose:** Publicar reversión existente con contrato HTTP sin alterar su semántica.

**Files:** Modify command service/schemas API; Test `backend/tests/test_human_review_b2b2_reversal.py`.

**Interfaces:** Consumes `append_functional_reversal(db, FunctionalReversalCommandV1)`; produces `revert_case`.

- [ ] RED: no RESEARCH_MANAGER, versión vieja, decisión de otro caso y snapshot incompleto fallan; reversión válida crea `reverted` y `functional_reversion`.
- [ ] Construir exactamente `FunctionalReversalCommandV1(review_item_id, decision_id_to_revert, expected_case_version, actor_user_id, reason, correlation_id, request_id)` desde request autenticado.
- [ ] Ejecutar PostgreSQL rollback/CAS/auditoría; GREEN/reviewer/checkpoint.

### Task 6: Proyección, ValidatedReadService y KPI

**Purpose:** Hacer efectiva una decisión humana en lectores y KPI tras commit.

**Files:** Modify `validated_read_service.py`, `kpi_service.py`, command service; Test `backend/tests/test_human_review_b2b2_readers.py`, `test_human_review_b2b2_kpi.py`.

- [ ] RED: override/identidad bloqueada cambia lectura y KPI inmediatamente; valor parser reconstruible contrario no gana.
- [ ] Introducir adaptadores de lectura por `stable_target_key` que usan proyección aprobada/locked y conservan datos fuente cuando no hay proyección; `KpiEffect` contiene solo métricas afectadas `{metric,before,after,delta}`.
- [ ] Ejecutar focal + `test_human_review_projection.py` + KPI actual; PostgreSQL 16 GREEN/reviewer/checkpoint.

### Task 7: Evidencia segura

**Purpose:** Entregar evidencia existente sin fuga de almacenamiento.

**Files:** Modify `backend/app/services/human_review_queries.py`; Create `backend/app/api/v1/endpoints/human_review.py` containing an unregistered evidence route; Test `backend/tests/test_human_review_b2b2_evidence.py`. Task 8 is the only task that imports/registers this router.

- [ ] RED: 403 sin capacidad, 404 sin caso, 503 backend fuente no disponible y cuerpo nunca contiene `source_path`/credenciales.
- [ ] Reusar el patrón `_stream_dropbox_pdf` de `endpoints/imports.py`, pero resolver el path internamente desde evidencia correlacionada y devolver nombre sanitizado, fragmento limitado y página/sección; límite `MAX_EVIDENCE_FRAGMENT_CHARS=4000`.
- [ ] GREEN con mocks de descarga, tamaño y mensajes seguros; reviewer/checkpoint.

### Task 8: Router e integración API

**Purpose:** Registrar endpoints B2B.2 y mapear servicios a HTTP/OpenAPI.

**Files:** Create `backend/app/api/v1/endpoints/human_review.py`; Modify `backend/app/api/v1/router.py`; Test `backend/tests/test_human_review_b2b2_api.py`.

- [ ] RED: GET queue/detail/audit/evidence, GET me, POST apply/discard/revert devuelven códigos 200/400/401/403/404/409/422/503 y correlation ID.
- [ ] Definir rutas exactas `/human-review/cases`, `/{id}`, `/{id}/audit`, `/{id}/evidence`, `/{id}/apply`, `/{id}/discard`, `/{id}/revert`, `/human-review/me`; usar `Depends(get_db)` y dependencia B2B.
- [ ] Ejecutar FastAPI+PostgreSQL y verificar OpenAPI; reviewer/checkpoint.

### Task 9: Tipos, API client y hooks frontend

**Purpose:** Consumir contratos API y preservar errores estructurados.

**Files:** Create `frontend/lib/human-review.ts`, `frontend/lib/human-review-api.ts`, `frontend/hooks/useHumanReview.ts`; Modify `frontend/lib/api.ts`, `frontend/lib/data-cache.tsx`; Test `frontend/tests/human-review-api.test.mjs`.

- [ ] RED: `apiFetch` conserva `{code,message,correlation_id}` y preview no llama `fetch`.
- [ ] Implementar `HumanReviewApiError`, tipos discriminados, `listCases/getCase/apply/discard/revert`, y `invalidate(prefix)` en cache; claves `human-review:queue:*`, `human-review:case:*`, `dashboard:*`.
- [ ] Ejecutar `npm test -- human-review-api.test.mjs` en `frontend`; GREEN/reviewer/checkpoint.

### Task 10: Bandeja y filtros frontend

**Purpose:** Presentar cola paginada y filtros realmente soportados.

**Files:** Create `frontend/app/human-review/page.tsx`, `components/human-review/ReviewQueueFilters.tsx`, `ReviewQueueTable.tsx`, `ReviewPagination.tsx`; Modify `AppShell.tsx`; Test `frontend/tests/human-review-queue.test.mjs`.

- [ ] RED: render loading/vacío/error, cambios de filtro reinician página y no se renderiza/envía carrera en esta ruta.
- [ ] Añadir enlace B2B visible solo tras `GET /human-review/me`; en `AppShell` aceptar prop `hideCareerFilter` y usarla en bandeja. Implementar filtros de §Global Constraints y tabla con `DataTable`/paginación API.
- [ ] Ejecutar test y `npm run build`; reviewer/checkpoint. **GATE D: no iniciar UI hasta contratos API de Task 8 cerrados.**

### Task 11: Detalle y evidencia frontend

**Purpose:** Mostrar detectado, normalizado, canónico/proyectado, overrides, memberships, evidencia e historial.

**Files:** Create `frontend/app/human-review/cases/[id]/page.tsx`, `frontend/components/human-review/DetectedDataPanel.tsx`, `frontend/components/human-review/EvidencePanel.tsx`, `frontend/components/human-review/AuditTimeline.tsx`; Test `frontend/tests/human-review-detail.test.mjs`.

- [ ] RED: 403/404/503 tienen mensaje seguro y el panel nunca muestra ruta interna.
- [ ] Usar `apiFetchBlob` para streaming autenticado; renderizar fragmento sanitizado/página/sección, decisión actual y timeline ascendente.
- [ ] GREEN/build/reviewer/checkpoint.

### Task 12: Formularios, preview y comandos frontend

**Purpose:** Aplicar decisiones desde un borrador exclusivamente local.

**Files:** Create `DecisionComposer.tsx`, `DecisionPreview.tsx`, `IdentityLinker.tsx`, `DiscardConfirmDialog.tsx`, `RevertConfirmDialog.tsx`; Test `frontend/tests/human-review-decisions.test.mjs`.

- [ ] RED: editar/cancelar/recargar no escribe; confirmación envía expected version/current decision/correlation ID; motivo obligatorio en acciones cerradas.
- [ ] Implementar `DecisionDraft` en `useState`; preview calcula la visualización sin invocar API; confirmación usa solo endpoints Task 8 e invalida keys Task 9.
- [ ] GREEN/build/reviewer/checkpoint.

### Task 13: Conflicto optimista y auditoría UI

**Purpose:** Resolver 409 sin pérdida de formulario y respetar capacidades.

**Files:** Create `OptimisticConflictDialog.tsx`, `CapabilityGate.tsx`; Modify detail/composer; Test `frontend/tests/human-review-conflict.test.mjs`.

- [ ] RED: 409 conserva draft en memoria, no reintenta, recarga explícitamente y reemplaza versión; acciones no autorizadas no aparecen.
- [ ] Mapear `REVIEW_CASE_VERSION_CONFLICT` a diálogo y refresco manual; renderizar audit con permiso `view_audit`.
- [ ] GREEN/build/reviewer/checkpoint.

### Task 14: E2E, rendimiento, seguridad y regresiones

**Purpose:** Verificar cadena completa y que ningún baseline disminuye.

**Files:** Create `backend/tests/test_human_review_b2b2_e2e.py`, `frontend/tests/human-review-e2e.test.mjs`; Modify no product file.

- [ ] Ejecutar flujo PostgreSQL: cola→detalle→evidencia→apply→ValidatedReadService/KPI→revert; dos comandos concurrentes; esperado una escritura ganadora y 409 perdedor.
- [ ] Ejecutar EXPLAIN de la cola crítica y fallar si no usa índices aplicables; scan de secretos de archivos B2B.2; comprobar que no existe `POST /proposals`.
- [ ] Ejecutar desde `backend`: `python -m unittest discover -s tests -v` esperado al menos baseline sin fallos/skips; `python -m compileall app`; desde `frontend`: `npm test; npm run build`; reviewer/checkpoint.

### Task 15: Aplicación controlada y cierre B2B.2

**Purpose:** Aplicar de manera reversible solo tras todas las pruebas y gates humanos.

**Files:** Create reportes bajo `backend/reports/human_review_b2b2_*`; Modify solo documentación/progreso autorizados.

**Preconditions:** **GATE B** antes de 0021 real, **GATE C** antes de capacidades reales, **GATE E** antes de deploy/smoke/cierre.

- [ ] Capturar estado read-only, backup SHA-256/TOC y restore PostgreSQL 16 descartable; verificar head 0020, KPI 2 y tablas científicas inmutables.
- [ ] Aplicar 0021/controlado, verificar catálogo, migración, permisos, audit, smoke con cuenta autorizada; si falla, rollback/restore y detener.
- [ ] Repetir suite, compileall, build, reviewer final Spec/Quality, cleanup de recursos descartables y reporte de cierre.

## TDD, PostgreSQL, Frontend y E2E

Cada tarea comienza en RED, ejecuta el comando focal, verifica el fallo esperado, aplica la mínima implementación, ejecuta GREEN y regresiones antes de checkpoint/reviewer. SQLite queda prohibido para locks, triggers, JSONB, constraints, permisos, migraciones y CAS concurrente; esas pruebas usan URL PostgreSQL 16 descartable. Frontend usa `node --test` existente y `next build`; el E2E invoca API contra PostgreSQL descartable y nunca la base real hasta Gate E.

## Human Gates and Risks

- Gate A: antes de crear 0021, enums/payloads/mapping audit; no autorizado por este plan.
- Gate B: antes de aplicar 0021 real.
- Gate C: antes de asignar/modificar capacidades reales.
- Gate D: antes de UI si Task 8 no cerró contratos API.
- Gate E: antes de deploy/smoke real/cierre.

Riesgos principales: regresión de constraint/auditoría (mitigada por upgrade/downgrade/re-upgrade y hashes), proyección ignorada por lectores (Task 6), fuga de Dropbox/MinIO (Task 7), CAS mal traducido (Tasks 4/13), filtros polimórficos no soportados (Task 3) e idempotencia sobreprometida (contratos Tasks 2/8). Cada uno tiene prueba focal y gate de parada.

## Plan Self-Review

- Cobertura: las 15 unidades cubren migración, contratos, queries, commands, reversión, proyección/KPI, evidencia, router, frontend, E2E y cierre.
- Consistencia: las interfaces backend se producen antes del router/frontend; frontend no inicia antes de Gate D.
- Scope: no hay borradores persistentes, reservas, nuevos filtros no respaldados, idempotencia fuerte ni funciones B2B.3–B2B.6.
- Seguridad: toda acción tiene capacidad backend, CAS, `correlation_id`, auditoría y error seguro; preview no emite request.
- 0021: sigue inexistente; este documento no crea código ni migraciones.
