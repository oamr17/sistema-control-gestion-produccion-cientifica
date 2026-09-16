# B2B.1 Human Review Foundations Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Construir y verificar exclusivamente las fundaciones persistentes de B2B.1: capacidades explícitas y deny-by-default, casos con target estable, decisiones y auditoría append-only, identidades/alias/overrides humanos, backfill inicial idempotente y rollback probado, sin habilitar acciones de revisión en API o frontend.

**Architecture:** B2B.1 agrega un núcleo relacional separado de las tablas reconstruibles de importación. La autoridad B2B se asigna mediante una tabla explícita de capacidades independiente de `users.role`; las decisiones y eventos son inmutables, mientras `review_items.current_decision_id` y los overrides materializan el estado vigente con control optimista. En esta fase la proyección se activa para persistir y verificar locks B1 importados, pero no se conecta todavía a `ValidatedReadService`, endpoints ni UI.

**Tech Stack:** Python 3.12, FastAPI 0.115.6, Pydantic 2.7.1, SQLAlchemy 2.0.36, psycopg 3.2.3, PostgreSQL 16, `unittest`, Docker Compose, `pg_dump`/`pg_restore`, PowerShell y el runner propio `schema_migrations`.

## Global Constraints

- Trabajar únicamente en `C:\Users\OMAR\Desktop\tesis\New project`.
- Este documento planifica B2B.1; su aprobación no autoriza ejecutar ninguna tarea.
- No crear endpoints, routers, pantallas, componentes frontend ni cambios en `ValidatedReadService` durante B2B.1.
- No introducir delegaciones, reservas, borradores, evidencia complementaria, cuarentena, conflictos de reprocesamiento, exportaciones ni acciones masivas visibles.
- No modificar geometría, parser, Dropbox, versión de Next.js ni herramienta antimalware.
- No cambiar `raw_name`, `raw_author_name`, `raw_title`, `raw_value`, `person_key`, `parsed_payload`, trazas, jobs, documentos, localizadores, `validation_status` ni datos científicos existentes.
- Mantener KPI base de productos elegibles exactamente en `2` antes y después de migración y backfill.
- `RESEARCH_MANAGER` representa al Gestor de Investigación; `SYSTEM_ADMIN` representa al Administrador técnico; `CAREER_MANAGER` carece siempre de capacidad B2B.
- No inferir capacidades por correo, actividad, nombre visible, `FACULTY_ADMIN` ni rol previo. La ausencia de una asignación activa implica denegación.
- No incluir correos de muestra en manifiestos, fixtures de asignación o comandos de apply real.
- Los JSON persistidos tienen `payload_schema`, `payload_version` y validación Pydantic `extra="forbid"`; no se aceptan objetos abiertos.
- Toda entidad mutable B2B lleva `version INTEGER NOT NULL DEFAULT 1`; toda escritura compara la versión esperada.
- `review_decisions` y `audit_events` no admiten `UPDATE`, `DELETE` ni `TRUNCATE` desde la cuenta de aplicación.
- Una corrección de auditoría o decisión se representa con una fila nueva y una referencia explícita a la fila corregida.
- El backfill ejecuta dry-run, aprobación por hash, apply transaccional y segunda ejecución con cero inserciones.
- Un fallo en cualquier gate global detiene la ejecución; no se continúa con una aceptación parcial.
- Al cerrar B2B.1 se detiene el trabajo antes de B2B.2.

---

## 1. Inspección confirmada y supuestos operativos

La inspección de planificación del 2026-07-13 confirmó:

- `backend/app/core/migrations.py` registra 16 revisiones y termina en `20260712_0016_canonical_identity_fields`.
- No existe archivo, import ni registro con identificadores 0017, 0018, 0019 o 0020. Por ello el plan reserva, sujeto al Gate G1, las revisiones `20260713_0017_b2b_capabilities`, `20260713_0018_human_review_core`, `20260713_0019_human_review_projection` y `20260713_0020_human_review_audit`.
- Existe un archivo histórico no registrado, `20260629_0002_add_scientific_production_authors.py`. La autoridad de ejecución actual es la lista `MIGRATIONS`; el inventario debe registrar ese archivo como legado sin incorporarlo silenciosamente a la cadena.
- `users.role` usa `UserRole` con `FACULTY_ADMIN` y `CAREER_MANAGER`. Extender ese enum o reclasificar filas produciría efectos laterales en servicios que tratan cualquier rol distinto de `CAREER_MANAGER` como alcance amplio. B2B.1 usa por ello una asignación de capacidad separada.
- `backend/app/models/entities.py` supera 36 KB. Las entidades B2B se crean en módulos enfocados y solo se exportan desde `backend/app/models/__init__.py`.
- El backend usa la misma URL para sesiones y migraciones, y Docker Compose apunta actualmente al superusuario `postgres`. B2B.1 separa `DATABASE_URL` de `MIGRATION_DATABASE_URL` antes de certificar permisos append-only.
- Las pruebas son `unittest`; las integraciones PostgreSQL existentes se activan con variables de entorno explícitas.
- Git CLI no está disponible en la sesión de planificación y `docker compose ps` no muestra contenedores activos. La ejecución vuelve a detectar ambos estados.
- Los respaldos existentes prueban el patrón custom-format, pero B2B.1 exige un backup nuevo, su hash y un restore nuevo inmediatamente antes del apply.

Si al ejecutar aparece una revisión posterior a 0016, G1 falla. No se renumeran archivos durante la marcha: se actualiza este plan y se solicita una nueva aprobación.

## 2. Límite de arquitectura B2B.1

### 2.1 Se activa en B2B.1

- Vocabularios cerrados y versionados.
- Generación determinista de `stable_target_key`.
- Persistencia de capacidades B2B explícitas.
- `review_items`, `review_decisions`, `field_overrides`, `canonical_identities`, `person_aliases` y `audit_events`.
- Triggers y permisos append-only.
- Lectura interna de la identidad humana proyectada para pruebas y backfill.
- Importación de `identity_locked=true` ya existente como decisión aprobada, identidad, overrides locales y evento de auditoría.
- Reversión funcional append-only invocable como servicio de dominio, sin endpoint.
- Contratos fundacionales de fusión y separación, sin ejecutar fusiones sobre datos reales.
- Dry-run y backfill idempotente de casos actuales.

### 2.2 Se conserva para una fase consumidora

| Fase | Dependencia recibida de B2B.1 | Sin tarea ejecutable en este plan |
|---|---|---|
| B2B.2 | casos, decisiones, overrides, proyección y control optimista | conexión con `ValidatedReadService`, API, bandejas, acciones, PDF y KPI transaccional |
| B2B.3 | capacidad `SYSTEM_ADMIN` deny-by-default y campos de versión | delegaciones, propuestas, reservas y borradores |
| B2B.4 | referencias estables de caso/decisión | assets, vínculos, seguridad, cuarentena y antimalware |
| B2B.5 | `stable_target_key`, locks humanos e historial | matching de reprocesamiento y conflictos por nueva evidencia |
| B2B.6 | auditoría inmutable y correlación | exportaciones PDF/XLSX/ZIP y cierre integral |

Los valores de enum aprobados para estados y tipos se fijan como contrato estable porque aparecen en constraints persistentes. Esto no crea comportamiento, endpoints ni tablas de fases posteriores. No se crean `review_delegations`, `review_reservations`, `review_drafts`, `evidence_assets`, `review_conflicts`, `review_exports` ni `review_action_batches`; todos admiten revisiones aditivas sin romper el esquema B2B.1.

## 3. Mapa exacto de archivos

### 3.1 Archivos que se crearán

| Ruta | Responsabilidad única |
|---|---|
| `backend/app/models/human_review_enums.py` | enums B2B persistentes y acciones base |
| `backend/app/models/human_review_access.py` | ORM de asignación explícita de capacidades |
| `backend/app/models/human_review_core.py` | ORM de `review_items` y `review_decisions` |
| `backend/app/models/human_review_projection.py` | ORM de identidades, alias y overrides |
| `backend/app/models/human_review_audit.py` | ORM de `audit_events` |
| `backend/app/schemas/human_review.py` | targets, valores y payloads cerrados de dominio |
| `backend/app/schemas/human_review_operations.py` | manifiestos tipados de cuentas, invariantes y backfill |
| `backend/app/services/human_review_targets.py` | normalización, hash raw y stable keys |
| `backend/app/services/human_review_invariants.py` | snapshots/hashes B1/B2A y comparación estricta |
| `backend/app/services/human_review_audit.py` | hash encadenado, append y corrección de eventos |
| `backend/app/services/human_review_authorization.py` | resolución deny-by-default y matriz base |
| `backend/app/services/human_review_capabilities.py` | dry-run/apply idempotente de cuentas aprobadas |
| `backend/app/services/human_review_state.py` | transiciones puras y error de versión |
| `backend/app/services/human_review_projection.py` | proyección interna, puntero vigente y reversión |
| `backend/app/services/human_review_backfill.py` | descubrimiento, plan y apply fundacional |
| `backend/app/migrations/versions/20260713_0017_b2b_capabilities.py` | tabla y constraints de capacidades |
| `backend/app/migrations/versions/20260713_0018_human_review_core.py` | casos, decisiones y append-only de decisiones |
| `backend/app/migrations/versions/20260713_0019_human_review_projection.py` | identidades, alias y overrides |
| `backend/app/migrations/versions/20260713_0020_human_review_audit.py` | ledger, hash chain y append-only de auditoría |
| `backend/scripts/configure_human_review_privileges.py` | grants/revokes para cuenta de aplicación existente |
| `backend/scripts/assign_b2b_capabilities.py` | CLI separado para asignación aprobada |
| `backend/scripts/backfill_human_review_b2b1.py` | CLI dry-run/apply del backfill |
| `backend/scripts/verify_human_review_b2b1.py` | inventario, ciclo de migración, invariantes y reporte |
| `backend/tests/support/__init__.py` | paquete de soporte de pruebas nuevas |
| `backend/tests/support/postgres.py` | schema PostgreSQL descartable común |
| `backend/tests/test_human_review_contracts.py` | contratos cerrados y stable targets |
| `backend/tests/test_human_review_invariants.py` | snapshots y preservación B1/B2A |
| `backend/tests/test_human_review_models.py` | mapeo ORM y ausencia de campos prohibidos |
| `backend/tests/test_human_review_migration_0017.py` | revisión de capacidades |
| `backend/tests/test_human_review_migration_0018.py` | revisión del núcleo |
| `backend/tests/test_human_review_migration_0019.py` | revisión de proyección |
| `backend/tests/test_human_review_migration_0020.py` | revisión de auditoría |
| `backend/tests/test_human_review_privileges.py` | cuenta owner/app y DML prohibido |
| `backend/tests/test_human_review_audit.py` | hash, cadena y correcciones |
| `backend/tests/test_human_review_authorization.py` | matriz Gestor/Admin/Career y manifiesto de cuentas |
| `backend/tests/test_human_review_projection.py` | versión, identidad, alias y reversión |
| `backend/tests/test_human_review_backfill.py` | dry-run, solapamiento, locks e idempotencia |

### 3.2 Archivos que se modificarán

| Ruta y ancla actual | Cambio acotado |
|---|---|
| `backend/app/core/config.py:5-24` | añadir `migration_database_url: str | None` |
| `backend/app/core/database.py:1-17` | construir `migration_engine` separado |
| `backend/app/main.py:8-30` | ejecutar `run_migrations(migration_engine)` |
| `backend/app/core/migrations.py:1-75` | importar y registrar 0017→0020 después de 0016 |
| `backend/app/models/__init__.py:1-45` | exportar las siete entidades B2B y la asignación de capacidad |
| `docker-compose.yml:27-45` | separar `DATABASE_URL` y `MIGRATION_DATABASE_URL` mediante variables de entorno obligatorias para el backend |

### 3.3 Artefactos generados durante una ejecución autorizada

Todos viven en `backend/reports/human_review_b2b1_<timestamp>/`:

- `inventory-before.csv`, `hashes-before.csv`, `migration-chain-before.txt`;
- `backup.dump`, `backup.dump.sha256`, `backup.toc.txt`, `restore-proof.txt`;
- `pre-b2b1-invariants.json`, `pre-b2b1-invariants.json.sha256`;
- `migration-upgrade-downgrade-reupgrade.json`;
- `backfill-dry-run.json`, `backfill-dry-run.json.sha256`, `backfill-approval.txt`;
- `backfill-apply.json`, `backfill-second-run.json`;
- `capabilities-dry-run.json`, `capabilities-apply.json`;
- `privilege-proof.txt`, `immutable-comparison.json`, `kpi-comparison.json`;
- `focused-tests.txt`, `postgres-tests.txt`, `full-suite.txt`, `compileall.txt`;
- `scope-files-after.csv`, `independent-spec-review.md`, `independent-quality-review.md`;
- `b2b1-delivery-report.md`.

### 3.4 Archivos que deben permanecer intactos

- Todo `frontend/`.
- Todo `backend/app/api/` y `backend/app/api/v1/endpoints/`.
- `backend/app/services/validated_read_service.py`.
- `backend/app/services/pdf_parser/`.
- `backend/app/services/dropbox_identity_reconciliation.py`.
- `backend/app/models/entities.py` y `backend/app/models/enums.py`.
- `backend/scripts/init_db.py`; las cuentas seed no reciben capacidad B2B.
- PDFs, backups históricos y reportes B1/B2A existentes.

## 4. Esquema fundacional exacto

Todas las PK nuevas son UUID, todos los timestamps son `TIMESTAMPTZ`, las FK usan `ON DELETE RESTRICT` salvo self-FK nullable, y los strings de estado usan `VARCHAR` + `CHECK` para que el downgrade sea reversible sin alterar tipos PostgreSQL compartidos.

### 4.1 `user_b2b_capabilities`

- Columnas: `id UUID`, `user_id INTEGER`, `capability VARCHAR(40)`, `is_active BOOLEAN`, `approval_reference VARCHAR(240)`, `approved_input_sha256 CHAR(64)`, `assigned_by_identifier VARCHAR(180)`, `assigned_at TIMESTAMPTZ`, `revoked_at TIMESTAMPTZ`, `revocation_reason TEXT`, `version INTEGER`.
- Checks: capability solo `RESEARCH_MANAGER|SYSTEM_ADMIN`; hash hexadecimal; activo implica `revoked_at IS NULL`; revocado implica fecha y motivo.
- Índices: único parcial por `user_id WHERE is_active`; `(capability,is_active)`.
- Triggers: rechazar asignación a un `users.role=CAREER_MANAGER`; rechazar cambiar un usuario con capacidad activa a `CAREER_MANAGER`.
- No se modifica `users.role`.

### 4.2 `review_items`

- Columnas: `id UUID`, `case_type VARCHAR(60)`, `stable_target_key VARCHAR(128)`, `target_table VARCHAR(80)`, `target_pk BIGINT`, `document_key VARCHAR(900)`, `source_revision VARCHAR(120)`, `source_page INTEGER`, `source_section VARCHAR(120)`, `row_or_block_id TEXT`, `field_path VARCHAR(120)`, `raw_value_sha256 CHAR(64)`, `period_id INTEGER`, `relationship_key VARCHAR(320)`, `case_status VARCHAR(40)`, `scientific_status VARCHAR(40)`, `automatic_priority SMALLINT`, `manual_priority SMALLINT`, `possible_kpi_impact BOOLEAN`, `current_decision_id UUID`, `version INTEGER`, `created_at`, `updated_at`.
- Case types persistidos: `person_identity`, `author_identity`, `product`, `project_director_relation`, `external_identity`, `possible_duplicate`, `invalid_text`, `new_evidence_conflict`. El último queda reservado como contrato, sin detector ni flujo B2B.5.
- Estados de caso: `pending`, `in_review`, `awaiting_gestor_approval`, `resolved`, `reopened`, `conflicted`, `superseded`.
- Estados científicos: `pending`, `validated`, `rejected`, `discarded`.
- Índices: bandeja `(case_type,case_status,automatic_priority,created_at)`, target, documento, periodo y decisión vigente.
- Unicidad parcial: un caso activo por `(case_type,stable_target_key)` para estados `pending|in_review|awaiting_gestor_approval|reopened|conflicted`.
- `current_decision_id` se agrega como FK diferible después de crear `review_decisions`.

### 4.3 `review_decisions`

- Columnas: `id UUID`, `review_item_id UUID`, `sequence INTEGER`, `decision_type VARCHAR(40)`, `decision_lifecycle VARCHAR(30)`, `scope VARCHAR(30)`, `payload_schema VARCHAR(80)`, `payload_version SMALLINT`, `payload JSONB`, `reason TEXT`, `actor_type VARCHAR(30)`, `actor_user_id INTEGER`, `actor_identifier VARCHAR(180)`, `actor_capability VARCHAR(40)`, `decided_at TIMESTAMPTZ`, `expected_case_version INTEGER`, `previous_decision_id UUID`, `corrects_decision_id UUID`, `locks_projection BOOLEAN`, `created_at TIMESTAMPTZ`.
- Tipos: `validated`, `corrected`, `linked`, `merged`, `maintained_separate`, `separated`, `rejected`, `discarded`, `maintained`, `reverted`.
- Ciclos: `proposed`, `approved`, `declined`, `superseded`.
- Scopes: `global_identity`, `record`, `document`, `relationship`, `period`.
- Checks: payload objeto y versión 1; secuencia positiva; approved que proyecta exige `locks_projection=true`; actor humano exige usuario/capacidad y actor legado exige identificador preservado.
- Único `(review_item_id,sequence)`; índices por caso, lifecycle, actor y decisiones relacionadas.
- Trigger `b2b_reject_append_only_mutation` bloquea UPDATE/DELETE/TRUNCATE.

### 4.4 `canonical_identities`

- Columnas: `id UUID`, `canonical_identity_key VARCHAR(320)`, `identity_type VARCHAR(40)`, `display_name VARCHAR(220)`, `status VARCHAR(30)`, `origin VARCHAR(30)`, `created_by_decision_id UUID`, `superseded_by_id UUID`, `version INTEGER`, `created_at`, `updated_at`.
- Tipos: `internal_person`, `external_person`, `unclassified_person`; estados `active|merged|superseded`; orígenes `b1_locked|human`.
- Clave canónica única. No contiene cédula, correo, roles, productos ni KPI.

### 4.5 `person_aliases`

- Columnas: `id UUID`, `alias_original VARCHAR(320)`, `alias_normalized VARCHAR(320)`, `alias_class VARCHAR(40)`, `canonical_identity_id UUID`, `decision_id UUID`, `scope VARCHAR(30)`, `status VARCHAR(30)`, `superseded_by_id UUID`, `version INTEGER`, `created_at`, `updated_at`.
- `alias_class=person_name`, `scope=global_identity`, estados `active|superseded`.
- Único parcial `(alias_normalized,alias_class) WHERE status='active'`.
- El ORM y la tabla carecen de columnas de rol, autoría, producto, periodo, estado científico o KPI.

### 4.6 `field_overrides`

- Columnas: `id UUID`, `review_item_id UUID`, `decision_id UUID`, `stable_target_key VARCHAR(128)`, `target_table VARCHAR(80)`, `target_pk BIGINT`, `field_path VARCHAR(120)`, `value_schema VARCHAR(80)`, `value_version SMALLINT`, `projected_value JSONB`, `scope VARCHAR(30)`, `document_key VARCHAR(900)`, `period_id INTEGER`, `relationship_key VARCHAR(320)`, `locked BOOLEAN`, `is_active BOOLEAN`, `valid_from TIMESTAMPTZ`, `superseded_by_id UUID`, `version INTEGER`, `created_at`, `updated_at`.
- Allowlist estructural de `field_path`: `canonical_identity_key`, `canonical_name`, `product_title`, `author_identity_key`, `project_director_identity_key`, `project_director_relationship_status`, `external_identity_key`, `external_institution`, `scientific_status`.
- Quedan estructuralmente fuera: cualquier campo que empiece por `raw_`, `person_key`, `parsed_payload`, `import_job_id`, `production_id`, `research_entity_id`, trazas, documento, página, sección, bounding box y evidencia extraída.
- `projected_value` es el wrapper escalar cerrado `ScalarOverrideValueV1`, no un objeto libre.
- Único override activo por target/campo/scope/contexto mediante índice funcional parcial.

### 4.7 `audit_events`

- Columnas: `id UUID`, `event_type VARCHAR(60)`, `aggregate_type VARCHAR(60)`, `aggregate_key VARCHAR(320)`, `review_item_id UUID`, `actor_user_id INTEGER`, `actor_identifier VARCHAR(180)`, `actor_capability VARCHAR(40)`, `occurred_at TIMESTAMPTZ`, `payload_schema VARCHAR(80)`, `payload_version SMALLINT`, `payload JSONB`, `correlation_id UUID`, `request_id UUID`, `previous_event_id UUID`, `corrects_event_id UUID`, `previous_event_hash CHAR(64)`, `event_hash CHAR(64)`, `created_at TIMESTAMPTZ`.
- Eventos B2B.1: `case_backfilled`, `locked_decision_imported`, `identity_created`, `alias_created`, `override_created`, `capability_assigned`, `capability_revoked`, `audit_corrected`, `functional_reversion`.
- Hash único; previous/corrects no pueden autorreferenciarse; un evento correctivo exige `corrects_event_id`.
- Índices por fecha, caso, actor, tipo, aggregate, correlation y corrects.
- Trigger append-only y permisos SQL sin UPDATE/DELETE/TRUNCATE.
- Retención indefinida: no existe tarea de purga.

## 5. Interfaces producidas y consumidas

### 5.1 Esquemas Pydantic exactos

Todos los esquemas siguientes heredan de una base con `ConfigDict(extra="forbid")`; los manifiestos y payloads aprobables añaden `frozen=True`. Ningún campo admite `Any` ni diccionarios abiertos.

| Clase | Campos exactos |
|---|---|
| `StableTargetV1` | `schema_version: Literal[1]`, `case_type: ReviewCaseType`, `target_table: ReviewTargetTable`, `target_pk: int | None`, `document_key: str`, `source_revision: str | None`, `source_page: int | None`, `source_section: str`, `row_or_block_id: str`, `field_path: OverrideField | Literal["case"]`, `raw_value_sha256: str`, `period_id: int | None`, `relationship_key: str | None`; hash exactamente `^[0-9a-f]{64}$` y `document_key`, `source_section`, `row_or_block_id` no vacíos ni solo whitespace, sin reescribir sus valores |
| `ScalarOverrideValueV1` | `kind: Literal["string","integer","decimal","boolean","null"]`, `string_value: str | None`, `integer_value: int | None`, `decimal_value: str | None`, `boolean_value: bool | None`; tipos estrictos, decimal string finito no vacío y model validator exige exactamente el campo correspondiente |
| `ProjectionAliasSnapshotV1` | `schema_version: Literal[1]`, `alias_original: str`, `alias_normalized: str`; ambos no vacíos |
| `ProjectionOverrideSnapshotV1` | `schema_version: Literal[1]`, `field_path: OverrideField`, `projected_value: ScalarOverrideValueV1`, `scope: OverrideScope`, `stable_target_key: str`, `target_table: ReviewTargetTable`, `target_pk: int | None`, `document_key: str | None`, `period_id: int | None`, `relationship_key: str | None`, `locked: bool` |
| `IdentityProjectionSnapshotV1` | `schema_version: Literal[1]`, `canonical_identity_key: str`, `canonical_name: str`, `identity_type: CanonicalIdentityType`, `aliases: tuple[ProjectionAliasSnapshotV1, ...]` |
| `ReviewProjectionSnapshotV1` | `schema_version: Literal[1]`, `case_status: ReviewCaseStatus`, `scientific_status: ScientificStatus`, `current_decision_id: UUID | None`, `identity: IdentityProjectionSnapshotV1 | None`, `overrides: tuple[ProjectionOverrideSnapshotV1, ...]` |
| `IdentityDecisionPayloadV1` | `kind: Literal["identity"]`, `schema_version: Literal[1]`, `canonical_identity_key: str`, `canonical_name: str`, `identity_type: CanonicalIdentityType`, `alias_original: str | None`, `alias_normalized: str | None` |
| `FieldOverridePayloadV1` | `kind: Literal["field_override"]`, `schema_version: Literal[1]`, `field_path: OverrideField`, `value: ScalarOverrideValueV1`, `scope: OverrideScope`, `stable_target_key: str | None`, `document_key: str | None`, `period_id: int | None`, `relationship_key: str | None` |
| `IdentityMergePayloadV1` | `kind: Literal["identity_merge"]`, `schema_version: Literal[1]`, `target_identity_key: str`, `member_stable_target_keys: tuple[str, ...]` con al menos dos miembros únicos |
| `IdentitySeparationMemberV1` | `stable_target_key: str`, `target_identity_key: str`, `target_canonical_name: str` |
| `IdentitySeparationPayloadV1` | `kind: Literal["identity_separation"]`, `schema_version: Literal[1]`, `source_identity_key: str`, `assignments: tuple[IdentitySeparationMemberV1, ...]` con al menos un target y `stable_target_key` únicos |
| `MaintainSeparatePayloadV1` | `kind: Literal["maintain_separate"]`, `schema_version: Literal[1]`, `stable_target_keys: tuple[str, ...]`, `identity_keys: tuple[str, ...]`; cada tuple tiene al menos dos miembros únicos y ambas longitudes coinciden |
| `DecisionReversalPayloadV1` | `kind: Literal["decision_reversal"]`, `schema_version: Literal[1]`, `decision_id_to_revert: UUID`, `restore_decision_id: UUID | None`, `projection_before: ReviewProjectionSnapshotV1`, `projection_after: ReviewProjectionSnapshotV1`; ambos snapshots son obligatorios |
| `HumanIdentityProjectionV1` | `stable_target_key: str`, `canonical_identity_key: str`, `canonical_name: str`, `decision_id: UUID`, `locked: Literal[True]`, `aliases: tuple[str, ...]` |
| `FunctionalReversalCommandV1` | `review_item_id: UUID`, `decision_id_to_revert: UUID`, `actor_user_id: int`, `expected_case_version: int`, `reason: str`, `correlation_id: UUID`, `request_id: UUID | None` |

`DecisionPayloadV1` es la unión discriminada por `kind` de los seis payloads de decisión. `IdentityDecisionPayloadV1`, `FieldOverridePayloadV1`, `IdentityMergePayloadV1`, `IdentitySeparationPayloadV1` y `MaintainSeparatePayloadV1` exponen además `projection_before: ReviewProjectionSnapshotV1 | None` y `projection_after: ReviewProjectionSnapshotV1 | None`: ambos están presentes o ambos ausentes. `DecisionReversalPayloadV1` exige el par. La futura capa de dominio exige el par para toda decisión aprobada con `locks_projection=true`; los payloads históricos sin snapshots siguen siendo legibles, pero no reversibles. `ReviewTargetTable` admite exactamente `person_roles`, `scientific_production_authors`, `scientific_productions`, `research_entities` y `external_researchers`.

Los snapshots se persisten exclusivamente dentro de `review_decisions.payload` JSONB; no existen columnas separadas y no se crea migración. Son cerrados, congelados y versionados. Los aliases se ordenan por `(alias_normalized, alias_original)` y rechazan `alias_normalized` duplicado. Los overrides se ordenan y deduplican por `(stable_target_key, field_path, scope, document_key, period_id, relationship_key)`. `stable_target_key` es obligatorio en cada snapshot porque identifica la fila proyectada que se recreará; `document_key`, `period_id` y `relationship_key` siguen la misma matriz de presencia/exclusión por scope que el override operativo. Los snapshots no admiten raw, parser/OCR, evidencia, roles, productos, autorías, relaciones, periodos ni KPI como colecciones o campos anexos.

La matriz cerrada de contexto de `FieldOverridePayloadV1` es:

| scope | contexto requerido | contexto prohibido | campos compatibles |
|---|---|---|---|
| `record` | `stable_target_key` no blank, máximo 128 y `^b2b:v1:[a-z_]+:[0-9a-f]{64}$` | `document_key`, `period_id`, `relationship_key` | todo `OverrideField` |
| `document` | `document_key` no blank | `stable_target_key`, `period_id`, `relationship_key` | todo `OverrideField` |
| `period` | `period_id` | `stable_target_key`, `document_key`, `relationship_key` | todo `OverrideField` |
| `relationship` | `relationship_key` no blank | `stable_target_key`, `document_key`, `period_id` | todo `OverrideField` |
| `global_identity` | ninguno | los cuatro contextos | solo `canonical_identity_key`, `canonical_name` |

Para `ProjectionOverrideSnapshotV1`, `stable_target_key` permanece siempre presente como localizador persistente del override. El contexto adicional es exactamente: ninguno para `record`; `document_key` para `document`; `period_id` para `period`; `relationship_key` para `relationship`; y ninguno para `global_identity`, que solo admite los dos campos canónicos de identidad. Cualquier contexto adicional o vacío se rechaza.

`normalize_person_alias` aplica NFKC y `casefold()`, conserva letras, marcas combinantes y números Unicode, convierte puntuación/símbolos/separadores Unicode en una frontera de espacio, colapsa whitespace y rechaza un resultado vacío. `build_stable_target_key` conserva exactamente `b2b:v1:{case_type.value}:{sha256_hex}` con los 64 caracteres hexadecimales minúsculos completos; `target_pk` es el único campo del modelo excluido del material canónico.

Los eventos usan payloads cerrados:

| Clase | Campos exactos además de `kind` y `schema_version: Literal[1]` |
|---|---|
| `CaseBackfilledAuditPayloadV1` | `review_item_id: UUID`, `case_type: ReviewCaseType`, `stable_target_key: str` |
| `LockedDecisionImportedAuditPayloadV1` | `decision_id: UUID`, `source_table: ReviewTargetTable`, `source_id: int`, `legacy_actor_identifier: str`, `legacy_decided_at: datetime` |
| `IdentityCreatedAuditPayloadV1` | `canonical_identity_id: UUID`, `canonical_identity_key: str`, `origin: Literal["b1_locked","human"]` |
| `AliasCreatedAuditPayloadV1` | `person_alias_id: UUID`, `canonical_identity_id: UUID`, `alias_normalized: str` |
| `OverrideCreatedAuditPayloadV1` | `field_override_id: UUID`, `field_path: OverrideField`, `stable_target_key: str` |
| `CapabilityChangedAuditPayloadV1` | `assignment_id: UUID`, `user_id: int`, `capability: B2BCapability`, `action: Literal["assigned","revoked"]`, `approved_input_sha256: str` |
| `AuditCorrectionPayloadV1` | `corrects_event_id: UUID`, `reason: str`, `replacement_payload_schema: str`, `replacement_payload_version: Literal[1]`, `replacement_payload_sha256: str` |
| `FunctionalReversalAuditPayloadV1` | `new_decision_id: UUID`, `reverted_decision_id: UUID`, `restored_decision_id: UUID | None`, `review_item_id: UUID` |

`AuditPayloadV1` es la unión discriminada de esos ocho payloads. `AuditEventCommandV1` contiene `id: UUID`, `event_type: AuditEventType`, `aggregate_type: str`, `aggregate_key: str`, `review_item_id: UUID | None`, `actor_user_id: int | None`, `actor_identifier: str`, `actor_capability: B2BCapability | None`, `occurred_at: datetime`, `payload: AuditPayloadV1`, `correlation_id: UUID`, `request_id: UUID | None`, `previous_event_id: UUID | None` y `corrects_event_id: UUID | None`. `AuditCorrectionCommandV1` contiene `original_event_id: UUID`, `actor_user_id: int`, `actor_identifier: str`, `actor_capability: B2BCapability`, `reason: str`, `replacement_payload_schema: str`, `replacement_payload_version: Literal[1]`, `replacement_payload_sha256: str`, `occurred_at: datetime`, `correlation_id: UUID` y `request_id: UUID | None`.

Los manifiestos operativos son:

| Clase | Campos exactos |
|---|---|
| `TableDigestV1` | `label: str`, `row_count: int`, `sha256: str` |
| `ParticipantMetricsV1` | `canonical_identities: int`, `participations: int`, `roles: int`, `authorships: int`, `pending: int`, `external_detected: int`, `external_kpi_eligible: int`, `external_pending: int` |
| `B2B1InvariantSnapshotV1` | `schema_version: Literal[1]`, `captured_at: datetime`, `migration_versions: tuple[str, ...]`, `digests: tuple[TableDigestV1, ...]`, `eligible_products: int`, `participant_metrics: ParticipantMetricsV1` |
| `ApprovedAccountAssignmentV1` | `user_id: int`, `email: EmailStr`, `capability: B2BCapability` |
| `ApprovedAccountAssignmentsV1` | `schema_version: Literal[1]`, `approval_reference: str`, `assignments: tuple[ApprovedAccountAssignmentV1, ...]` |
| `CapabilityAssignmentPlanEntryV1` | campos de assignment + `action: Literal["insert","unchanged","conflict"]`, `reason: str` |
| `CapabilityAssignmentPlanV1` | `schema_version: Literal[1]`, `manifest_sha256: str`, `entries: tuple[CapabilityAssignmentPlanEntryV1, ...]`, `insert_count: int`, `unchanged_count: int`, `conflict_count: int` |
| `CapabilityAssignmentResultV1` | `schema_version: Literal[1]`, `manifest_sha256: str`, `inserted: int`, `unchanged: int`, `audit_events_created: int` |
| `SourcePopulationCountV1` | `population: BackfillSourceMembership`, `row_count: int`, `stable_target_count: int` |
| `SourcePopulationOverlapV1` | `populations: tuple[BackfillSourceMembership, ...]`, `stable_target_count: int` |
| `BackfillCandidateV1` | `case_type: ReviewCaseType`, `stable_target: StableTargetV1`, `source_table: ReviewTargetTable`, `source_id: int`, `memberships: tuple[BackfillSourceMembership, ...]`, `case_status: ReviewCaseStatus`, `scientific_status: ScientificStatus`, `possible_kpi_impact: bool` |
| `LockedDecisionImportV1` | `candidate: BackfillCandidateV1`, `canonical_identity_key: str`, `canonical_name: str`, `identity_type: CanonicalIdentityType`, `legacy_actor_identifier: str`, `legacy_decided_at: datetime`, `alias_original: str | None` |
| `BackfillBlockerV1` | `source_table: ReviewTargetTable`, `source_id: int`, `code: Literal["missing_stable_locator","incomplete_locked_decision","ambiguous_alias","invariant_mismatch","ambiguous_project_evidence","contradictory_project_evidence"]`, `message: str` |
| `BackfillDeferredRecordV1` | `source_table: Literal[ReviewTargetTable.PERSON_ROLES]`, `source_pk: int`, `source_population: Literal[BackfillSourceMembership.CANONICAL_PENDING]`, `case_type: Literal[ReviewCaseType.PERSON_IDENTITY]`, `period_id: int`, `document_key: str`, `identity_reference: str`, `reason_code: Literal["missing_row_locator"]`, `disposition: Literal["expected_pending_review"]`, `materializable: Literal[False]`, `affects_kpi: Literal[False]`, `evidence_summary: str` |
| `BackfillExcludedRecordV1` | `source_table: Literal[ReviewTargetTable.RESEARCH_ENTITIES]`, `research_entity_id: int`, `source_population: Literal[BackfillSourceMembership.DIRECTOR_RELATION_PENDING]`, `case_type: Literal[ReviewCaseType.PROJECT_DIRECTOR_RELATION]`, `period_id: int`, `entity_type: Literal["grupo_investigacion","semillero"]`, `reason_code: Literal["entity_type_incompatible"]`, `disposition: Literal["out_of_scope_record"]`, `materializable: Literal[False]`, `affects_kpi: Literal[False]`, `evidence_summary: str` |
| `B2B1BackfillPlanV1` | `schema_version: Literal[1]`, `captured_at: datetime`, `invariant_snapshot_sha256: str`, `source_counts: tuple[SourcePopulationCountV1, ...]`, `overlaps: tuple[SourcePopulationOverlapV1, ...]`, `union_stable_target_count: int`, `candidates: tuple[BackfillCandidateV1, ...]`, `locked_decisions: tuple[LockedDecisionImportV1, ...]`, `blockers: tuple[BackfillBlockerV1, ...]`, `deferred_records: tuple[BackfillDeferredRecordV1, ...] = ()`, `excluded_records: tuple[BackfillExcludedRecordV1, ...] = ()`, `hard_blockers: tuple[BackfillBlockerV1, ...] = ()` |
| `B2B1BackfillApplyResultV1` | `schema_version: Literal[1]`, `plan_sha256: str`, `before_invariants_sha256: str`, `after_invariants_sha256: str`, `created_review_items: int`, `created_decisions: int`, `created_overrides: int`, `created_identities: int`, `created_aliases: int`, `created_audit_events: int`, `eligible_products_before: int`, `eligible_products_after: int` |

`BackfillSourceMembership` admite `canonical_pending`, `product_pending`, `director_relation_pending`, `external_pending`, `possible_match` y `identity_locked`. Los errores públicos son `B2BAccessDenied(PermissionError)`, `OptimisticLockError(RuntimeError)`, `InvariantViolation(RuntimeError)` y `BackfillGateError(RuntimeError)`.

Los contratos de backfill son cerrados, congelados y versionados cuando corresponde. Ordenan canónicamente sus tuples, rechazan duplicados y validan conteos: `stable_target_count <= row_count`; overlaps con al menos dos poblaciones únicas; source counts únicos; candidatos y locks únicos por `(case_type, stable_target_key)`; blockers únicos por `(source_table, source_id, code)`; deferred y excluded únicos por su referencia de fuente; `hard_blockers` idéntico a `blockers`; colecciones materializables, deferred, excluded y blockers sin solapamiento de referencia; y `union_stable_target_count` igual a la unión real de stable targets, nunca a la suma de poblaciones. Los tres campos aditivos v3 usan tuplas vacías predeterminadas, por lo que los planes históricos sin `deferred_records`, `excluded_records` o `hard_blockers` continúan validando; cuando falta `hard_blockers`, se deriva exclusivamente del campo legado `blockers`.

La matriz cerrada de población y fuente es: `canonical_pending` sobre `person_roles` → `person_identity`; `canonical_pending` sobre `scientific_production_authors` → `author_identity`; `product_pending` sobre `scientific_productions` → `product`; `director_relation_pending` sobre `research_entities`, con `research_projects` solo como evidencia, → `project_director_relation`; `external_pending` sobre `external_researchers` → `external_identity`; y `possible_match` sobre rol o autor → `possible_duplicate`. `identity_locked` conserva el tipo de identidad de su fuente y convierte únicamente ese candidato en `resolved/validated`. `possible_match` siempre crea un candidato separado: su candidato usa `case_type=possible_duplicate`, mientras su `StableTargetV1` conserva `person_identity` o `author_identity` según la fuente para reutilizar exactamente la stable key del caso de identidad. La unicidad `(case_type, stable_target_key)` mantiene ambos casos separados. Toda combinación no enumerada falla con `BackfillGateError` o blocker.

Para `director_relation_pending`, la evidencia externa de `research_projects` del mismo periodo tiene prioridad y exige igualdad normalizada exacta de código o nombre; coincidencias múltiples o contradictorias generan blocker. Si no existe coincidencia externa, únicamente un `proyecto_fci` con procedencia estable, código o nombre informativo y evidencia propia única del mismo periodo puede materializarse mediante self-evidence, siempre como caso pendiente y sin decisión automática. `grupo_investigacion` y `semillero` se registran como excluded y no se materializan. La evidencia debe aportar documento y sección, su clave estable de código/nombre entra en el `relationship_key`, no se incorpora ningún PK técnico al material estable y el target operativo continúa siendo `research_entities`.

Una importación `identity_locked` usa IDs UUIDv5 por dominios separados y `IdentityDecisionPayloadV1`. Su `projection_before` exacto es `reopened/pending`, sin puntero, identidad ni overrides. Su `projection_after` exacto es `resolved/validated`, apunta a la decisión UUIDv5 e incluye identidad/aliases compatibles y dos overrides record bloqueados (`canonical_identity_key`, `canonical_name`). La decisión es `validated/approved`, `locks_projection=true`, `sequence=1`, `expected_case_version=1`, actor `legacy`, sin predecessor ni corrección. El caso, decisión y FK diferible se crean atómicamente en estado final; no se usa una transición `pending → resolved`.

Varios locks con la misma `canonical_identity_key` mantienen decisiones y overrides independientes, pero comparten la identidad global. El primer lock según el orden canónico del plan posee `created_by_decision_id` y el único evento `identity_created`; la primera aparición de cada alias normalizado posee el alias y el único `alias_created`. Los locks posteriores reutilizan ambos sin perder sus snapshots propios ni crear duplicados.

### 5.2 Firmas de servicios

| Módulo | Firma exacta producida | Consumidor B2B.1 |
|---|---|---|
| `human_review_targets` | `normalize_person_alias(value: str) -> str` | payloads, alias y backfill |
|  | `raw_value_sha256(value: str | None) -> str` | target e invariantes |
|  | `build_stable_target_key(target: StableTargetV1) -> str` | backfill y unicidad |
| `human_review_invariants` | `capture_b2b1_invariants(connection: Connection) -> B2B1InvariantSnapshotV1` | dry-run, apply, entrega |
|  | `compare_b2b1_invariants(before: B2B1InvariantSnapshotV1, after: B2B1InvariantSnapshotV1) -> None` | gates de preservación |
| `human_review_audit` | `compute_audit_event_hash(command: AuditEventCommandV1, previous_hash: str | None) -> str` | append audit |
|  | `append_audit_event(db: Session, command: AuditEventCommandV1) -> AuditEvent` | cuentas, backfill, reversión |
|  | `append_audit_event_at_current_head(db: Session, command: AuditEventCommandV1) -> AuditEvent` | backfill y reversión cuando el predecessor se resuelve internamente |
|  | `append_audit_correction(db: Session, command: AuditCorrectionCommandV1) -> AuditEvent` | corrección inmutable |
| `human_review_authorization` | `resolve_b2b_capability(db: Session, user_id: int) -> B2BCapability | None` | policy y cuenta CLI |
|  | `authorize_b2b_action(db: Session, user: User, action: B2BAction) -> B2BCapability` | servicios internos |
| `human_review_capabilities` | `plan_capability_assignments(db: Session, manifest: ApprovedAccountAssignmentsV1) -> CapabilityAssignmentPlanV1` | dry-run CLI |
|  | `apply_capability_assignments(db: Session, manifest: ApprovedAccountAssignmentsV1, approved_manifest_sha256: str, operator_identifier: str) -> CapabilityAssignmentResultV1` | apply real gated |
| `human_review_state` | `assert_case_transition(current: ReviewCaseStatus, target: ReviewCaseStatus) -> None` | proyección y pruebas |
|  | `assert_expected_version(actual: int, expected: int) -> None` | toda mutación mutable |
| `human_review_projection` | `load_identity_projection(db: Session, stable_target_key: str) -> HumanIdentityProjectionV1 | None` | pruebas y backfill |
|  | `set_current_decision(db: Session, review_item_id: UUID, decision_id: UUID, expected_version: int, case_status: ReviewCaseStatus, scientific_status: ScientificStatus) -> ReviewItem` | import/reversión |
|  | `append_functional_reversal(db: Session, command: FunctionalReversalCommandV1) -> ReviewDecision` | reversión B2B.1 sin endpoint |
| `human_review_backfill` | `load_backfill_candidates(db: Session) -> tuple[BackfillCandidateV1, ...]` | dry-run |
|  | `build_backfill_plan(db: Session, captured_at: datetime) -> B2B1BackfillPlanV1` | CLI dry-run |
|  | `apply_backfill_plan(db: Session, plan: B2B1BackfillPlanV1, approved_plan_sha256: str, expected_invariants_sha256: str) -> B2B1BackfillApplyResultV1` | CLI apply |

Los scripts exponen estos CLIs exactos:

```powershell
python scripts/backfill_human_review_b2b1.py --dry-run --output "$reportDir/backfill-dry-run.json"
python scripts/backfill_human_review_b2b1.py --apply --plan "$reportDir/backfill-dry-run.json" --approved-plan-sha256 $planHash --snapshot "$reportDir/pre-b2b1-invariants.json" --backup "$reportDir/backup.dump" --backup-sha256 $backupHash --output "$reportDir/backfill-apply.json"
python scripts/assign_b2b_capabilities.py --dry-run --manifest "$reportDir/approved-account-assignments.json" --output "$reportDir/capabilities-dry-run.json"
python scripts/assign_b2b_capabilities.py --apply --manifest "$reportDir/approved-account-assignments.json" --approved-manifest-sha256 $accountsHash --operator-identifier $env:B2B1_OPERATOR_IDENTIFIER --output "$reportDir/capabilities-apply.json"
python scripts/configure_human_review_privileges.py --owner-url-env MIGRATION_DATABASE_URL --application-role-env B2B1_APPLICATION_DB_ROLE --apply
python scripts/verify_human_review_b2b1.py --database-url-env MIGRATION_DATABASE_URL --report-dir $reportDir --expected-head 20260713_0020_human_review_audit
```

Ningún CLI acepta una URL con contraseña como argumento; las conexiones provienen de variables de entorno.

## 6. Dependencias entre tareas

| Tarea | Depende de | Gate de salida |
|---|---|---|
| 1 | especificación aprobada | contratos cerrados verdes |
| 2 | 1 | snapshot determinista y KPI 2 |
| 3 | 1 | ORM exacto sin modificar `entities.py` |
| 4 | 3 | revisión 0017 reversible |
| 5 | 4 | revisión 0018 reversible y decisiones append-only |
| 6 | 5 | revisión 0019 reversible y raw prohibido |
| 7 | 6 | revisión 0020 reversible y auditoría append-only |
| 8 | 7 | cuenta app no-superuser sin DML destructivo |
| 9 | 7 | hash/corrección auditables |
| 10 | 4, 8, 9 | autorización deny-by-default y asignación idempotente |
| 11 | 5, 6, 9, 10 | versión, proyección y reversión verdes |
| 12 | 2, 5, 6, 9, 11 | dry-run/apply idempotente en pruebas |
| 13 | 4–12 | backup restaurable y ciclo 0017→0020 probado |
| 14 | 13 + cuentas aprobadas | apply real, segunda corrida cero, capacidades exactas |
| 15 | 14 | Spec PASS / Quality PASS e informe; detenerse |

## 7. Gates globales de detención

| Gate | Condición de aprobación | Evidencia |
|---|---|---|
| G0 Alcance | solo archivos del mapa B2B.1 | inventario/hash o `git diff --name-only` |
| G1 Cadena | head real 0016 antes de cambios; 0017–0020 libres; DB y registry compatibles | `migration-chain-before.txt` |
| G2 Backup | custom dump tiene hash, TOC y restore que abre y consulta | `restore-proof.txt` |
| G3 PostgreSQL | todas las pruebas PG ejecutadas, ninguna omitida | `postgres-tests.txt` sin `skipped` B2B.1 |
| G4 Reversibilidad | upgrade/downgrade/re-upgrade 0017–0020 verde en restore descartable | ciclo JSON |
| G5 Dry-run | hash y alcance revisados; solapamientos reportados sin sumar poblaciones | `backfill-approval.txt` |
| G6 Idempotencia | segunda ejecución crea 0 casos, 0 decisiones, 0 overrides, 0 identidades, 0 alias y 0 eventos | `backfill-second-run.json` |
| G7 Inmutables | hashes raw/trazas/payloads/jobs/ciencia sin cambio | `immutable-comparison.json` |
| G8 KPI | productos elegibles antes=2 y después=2 | `kpi-comparison.json` |
| G9 Career | ningún `CAREER_MANAGER` tiene capacidad o acción B2B | pruebas + consulta SQL |
| G10 Admin | `SYSTEM_ADMIN` no puede proponer/aplicar ciencia sin una delegación B2B.3 inexistente | matriz negativa |
| G11 Auditoría | app role no actualiza, elimina ni trunca decisions/audit | `privilege-proof.txt` |
| G12 Alias | alias solo cambia identidad; roles/productos/estados/KPI iguales | prueba PG + invariantes |

## 8. Git y evidencia equivalente

### Ruta A: Git disponible

Antes de la Tarea 1:

```powershell
git --version
git status --short
git rev-parse --show-toplevel
```

Requerir que el toplevel sea exactamente `C:/Users/OMAR/Desktop/tesis/New project`. Cada tarea usa el mensaje de commit indicado, después de sus pruebas verdes y revisión independiente. No se hace un commit que mezcle dos tareas.

### Ruta B: Git no disponible

Definir una sola vez:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$reportDir = Join-Path (Resolve-Path 'backend/reports') "human_review_b2b1_$stamp"
New-Item -ItemType Directory -Force -Path (Join-Path $reportDir 'checkpoints') | Out-Null
rg --files backend docker-compose.yml | Sort-Object | Set-Content -Encoding UTF8 (Join-Path $reportDir 'inventory-before.csv')
Get-FileHash -Algorithm SHA256 (rg --files backend docker-compose.yml) |
  Sort-Object Path |
  Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $reportDir 'hashes-before.csv')
```

En cada tarea `NN`:

1. crear `checkpoints/task-NN/backup`;
2. copiar allí cada archivo que será modificado antes del cambio;
3. guardar `files-before.csv` y `files-after.csv` con SHA-256;
4. guardar salida RED y GREEN completa;
5. pedir revisión independiente de requisito y calidad;
6. escribir `review.md` con resultado `Spec PASS` y `Quality PASS` o detenerse.

Estos checkpoints son equivalentes operativos a commits; no se afirma que exista historial Git.

---

## Task 1: Contratos cerrados y stable targets

**Files:**
- Create: `backend/app/models/human_review_enums.py`
- Create: `backend/app/schemas/human_review.py`
- Create: `backend/app/services/human_review_targets.py`
- Create: `backend/tests/test_human_review_contracts.py`

**Interfaces:** Produce todos los enums, `StableTargetV1`, la unión discriminada `DecisionPayloadV1`, payloads de fusión/separación/reversión y las tres funciones de target. No consume ORM ni PostgreSQL.

- [ ] **Step 1: Escribir RED para payload cerrado, raw prohibido y stable key determinista**

```python
class HumanReviewContractTests(unittest.TestCase):
    def test_payload_rejects_unknown_key(self):
        with self.assertRaises(ValidationError):
            IdentityDecisionPayloadV1.model_validate({
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": "human:one",
                "canonical_name": "Persona Uno",
                "unexpected": True,
            })

    def test_raw_field_cannot_be_override_target(self):
        for field in ("raw_name", "raw_author_name", "raw_title", "raw_value", "person_key", "parsed_payload"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                FieldOverridePayloadV1(field_path=field, value=ScalarOverrideValueV1(kind="string", string_value="x"))

    def test_stable_key_ignores_surrogate_row_id(self):
        first = StableTargetV1.model_validate(self.target | {"target_pk": 10})
        second = StableTargetV1.model_validate(self.target | {"target_pk": 99})
        self.assertEqual(build_stable_target_key(first), build_stable_target_key(second))
```

- [ ] **Step 2: Ejecutar RED**

Run desde `backend/`:

```powershell
python -m unittest tests.test_human_review_contracts -v
```

Expected: FAIL por módulos `human_review_enums`, `human_review` y `human_review_targets` ausentes.

- [ ] **Step 3: Implementar el mínimo contractual**

Crear enums con los valores exactos de las secciones 4.2–4.7. Todos los modelos Pydantic usan:

```python
model_config = ConfigDict(extra="forbid", frozen=True)
```

`StableTargetV1` exige `schema_version: Literal[1]`, case type, target table/PK informativo, `document_key`, localizadores, field path, hash raw, periodo y relationship key. El material hasheado excluye `target_pk` y contiene únicamente campos estables. La clave exacta es:

```python
digest = sha256(canonical_json).hexdigest()
return f"b2b:v1:{target.case_type.value}:{digest}"
```

`ScalarOverrideValueV1` admite exactamente un valor entre string, integer, decimal serializado como string, boolean o null. La unión `DecisionPayloadV1` se discrimina por `kind` y contiene `IdentityDecisionPayloadV1`, `IdentityMergePayloadV1`, `IdentitySeparationPayloadV1`, `MaintainSeparatePayloadV1`, `FieldOverridePayloadV1` y `DecisionReversalPayloadV1`. Los payloads de merge/separate solo aceptan claves de identidad y targets; no tienen campos de rol, producto, estado o KPI.

- [ ] **Step 4: Ejecutar GREEN y revisar determinismo**

```powershell
python -m unittest tests.test_human_review_contracts -v
```

Expected: PASS; dos serializaciones con distinto orden de entrada producen la misma key; una variación de documento, página, sección, bloque, field path o hash raw produce otra key.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que no existe `dict[str, Any]` en los payloads persistidos y que ningún raw field pertenece a `OverrideField`. Commit si Git está disponible:

```powershell
git add backend/app/models/human_review_enums.py backend/app/schemas/human_review.py backend/app/services/human_review_targets.py backend/tests/test_human_review_contracts.py
git commit -m "feat: define closed b2b1 review contracts"
```

## Task 2: Manifiestos operativos e invariantes B1/B2A

**Files:**
- Create: `backend/app/schemas/human_review_operations.py`
- Create: `backend/app/services/human_review_invariants.py`
- Create: `backend/tests/test_human_review_invariants.py`

**Interfaces:** Produce manifiestos Pydantic cerrados y captura de hashes. Consume `ValidatedReadService.production_views(visibility="eligible")` solo en lectura; no modifica el lector.

- [ ] **Step 1: Escribir RED para snapshot determinista**

Probar que `capture_b2b1_invariants()` incluye conteo/hash de roles, autores, producciones, entidades, proyectos, externos, jobs, trazas, payloads y estados, además de `eligible_products=2`; probar que cualquier diferencia hace fallar `compare_b2b1_invariants()` con las etiquetas exactas cambiadas.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_invariants -v
```

Expected: FAIL por manifiestos y servicio ausentes.

- [ ] **Step 3: Implementar captura de solo lectura**

Definir `TableDigestV1(label: str, row_count: int, sha256: str)`, `B2B1InvariantSnapshotV1(schema_version=1, captured_at, migration_versions, digests, eligible_products, participant_metrics)` y `snapshot_sha256()`. Seleccionar columnas protegidas con ORDER BY PK, serializar fechas/UUID/JSON de forma canónica y calcular SHA-256 en Python. Ejecutar dentro de `SET TRANSACTION READ ONLY` y comparar snapshot al inicio/final de la propia captura.

- [ ] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_invariants -v
```

Expected: PASS; ninguna prueba escribe tablas y los hashes son estables.

- [ ] **Step 5: Revisión y checkpoint**

Verificar que la lista protegida incluye `identity_locked`, `identity_decided_by` e `identity_decided_at`, y que no considera las nuevas tablas B2B en el hash de ciencia. Commit:

```powershell
git add backend/app/schemas/human_review_operations.py backend/app/services/human_review_invariants.py backend/tests/test_human_review_invariants.py
git commit -m "test: lock b1 b2a invariants for b2b1"
```

## Task 3: Modelos ORM fundacionales separados

**Files:**
- Create: `backend/app/models/human_review_access.py`
- Create: `backend/app/models/human_review_core.py`
- Create: `backend/app/models/human_review_projection.py`
- Create: `backend/app/models/human_review_audit.py`
- Modify: `backend/app/models/__init__.py:1-45`
- Create: `backend/tests/test_human_review_models.py`

**Interfaces:** Produce `UserB2BCapability`, `ReviewItem`, `ReviewDecision`, `CanonicalIdentity`, `PersonAlias`, `FieldOverride` y `AuditEvent`.

- [ ] **Step 1: Escribir RED de columnas, tipos y ausencia de campos prohibidos**

Usar `class_mapper()` para verificar cada columna de la sección 4, UUID, timezone, JSONB, defaults de version, FKs y `current_decision_id`. Afirmar explícitamente que `PersonAlias` carece de `role`, `product_id`, `period_id`, `scientific_status` y `kpi`; y que `CanonicalIdentity` carece de cédula/correo.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_models -v
```

Expected: FAIL porque las clases no existen.

- [ ] **Step 3: Implementar los modelos sin ampliar `entities.py`**

Usar `sqlalchemy.Uuid(as_uuid=True)`, `JSON` con variante PostgreSQL `JSONB`, `DateTime(timezone=True)`, `CheckConstraint`, `Index` y relaciones por nombre. `models/__init__.py` importa y exporta exactamente las siete clases. No añadir relaciones inversas a `User` ni cambiar modelos científicos.

- [ ] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_models -v
```

Expected: PASS; `Base.metadata.tables` contiene las siete tablas nuevas más `user_b2b_capabilities` al importar `app.models`.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que los constraints ORM coinciden con la sección 4 y que ningún archivo existente grande fue ampliado. Commit:

```powershell
git add backend/app/models/__init__.py backend/app/models/human_review_access.py backend/app/models/human_review_core.py backend/app/models/human_review_projection.py backend/app/models/human_review_audit.py backend/tests/test_human_review_models.py
git commit -m "feat: add focused b2b1 persistence models"
```

## Task 4: Revisión 0017 de capacidades B2B

**Files:**
- Create: `backend/app/migrations/versions/20260713_0017_b2b_capabilities.py`
- Modify: `backend/app/core/migrations.py:1-75`
- Create: `backend/tests/support/__init__.py`
- Create: `backend/tests/support/postgres.py`
- Create: `backend/tests/test_human_review_migration_0017.py`

**Interfaces:** La revisión exporta `VERSION`, `revision`, `down_revision`, `upgrade(engine: Engine) -> None`, `downgrade(engine: Engine) -> None` y `assert_schema(connection: Connection) -> None`.

- [ ] **Step 1: Repetir G1 antes de crear el archivo**

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend')
Set-Location backend
python -c "from app.core.migrations import MIGRATIONS; print(MIGRATIONS[-1][0]); print(len(MIGRATIONS))"
rg -n "0017|0018|0019|0020" app/migrations app/core/migrations.py
```

Expected: head `20260712_0016_canonical_identity_fields`, count `16`, y `rg` sin matches. Si difiere, detenerse.

- [ ] **Step 2: Escribir RED PostgreSQL**

Las pruebas crean un schema UUID en `B2B1_TEST_DATABASE_URL`, siembran `users` y las 16 revisiones anteriores, y exigen tabla/constraints/índices/triggers, rechazo a Career, downgrade limpio y ausencia de registro cuando `assert_schema` falla.

- [ ] **Step 3: Ejecutar RED sin permitir skip**

```powershell
$env:B2B1_TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/science_faculty_b2b1_test'
python -m unittest tests.test_human_review_migration_0017 -v
```

Expected: FAIL por módulo 0017 ausente. Si aparece `skipped`, G3 falla.

- [ ] **Step 4: Implementar upgrade/downgrade transaccional**

Metadatos exactos:

```python
VERSION = "20260713_0017_b2b_capabilities"
revision = VERSION
down_revision = "20260712_0016_canonical_identity_fields"
```

Crear tabla, índices y las dos funciones/trigger de Career en un único `engine.begin()`. `assert_schema(connection)` consulta `pg_constraint`, `pg_indexes` y `pg_trigger` dentro de la misma transacción; cualquier ausencia lanza `RuntimeError`, revierte DDL y evita que el runner inserte la versión. Downgrade elimina triggers, funciones y tabla, en ese orden.

- [ ] **Step 5: Registrar 0017 después de 0016 y ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_migration_0017 -v
```

Expected: PASS, incluida prueba `upgrade → downgrade → upgrade` y registro único.

- [ ] **Step 6: Revisión y checkpoint**

Confirmar que no hay INSERT de cuentas ni inferencia desde `users`. Commit:

```powershell
git add backend/app/core/migrations.py backend/app/migrations/versions/20260713_0017_b2b_capabilities.py backend/tests/support backend/tests/test_human_review_migration_0017.py
git commit -m "feat: add reversible b2b capability migration"
```

## Task 5: Revisión 0018 del núcleo de casos y decisiones

**Files:**
- Create: `backend/app/migrations/versions/20260713_0018_human_review_core.py`
- Modify: `backend/app/core/migrations.py`
- Create: `backend/tests/test_human_review_migration_0018.py`

**Interfaces:** Mismo contrato de migración; `down_revision="20260713_0017_b2b_capabilities"`.

- [ ] **Step 1: Escribir RED PostgreSQL**

Probar tablas/columnas/checks, unique activo por tipo+stable key, FK circular diferible, sequence única, rechazo de payload no-objeto, trigger append-only y rollback completo cuando falta un índice o constraint esperado.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_migration_0018 -v
```

Expected: FAIL por revisión 0018 ausente; cero skips.

- [ ] **Step 3: Implementar la revisión**

Metadatos:

```python
VERSION = "20260713_0018_human_review_core"
revision = VERSION
down_revision = "20260713_0017_b2b_capabilities"
```

Crear `review_items` con `stable_target_key VARCHAR(128)`, luego `review_decisions`, luego FK `review_items.current_decision_id`; crear `b2b_reject_append_only_mutation()` y trigger de decisiones. `assert_schema` valida todos los objetos de 4.2/4.3 dentro de la transacción. Downgrade elimina FK, trigger, decisiones, casos y función; rechaza downgrade si 0020 sigue aplicado.

- [ ] **Step 4: Registrar y ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_migration_0018 -v
```

Expected: PASS; un UPDATE/DELETE de decisión falla con `review_decisions is append-only`.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que no existen columnas de delegación, reserva, evidencia o KPI materializado. Commit:

```powershell
git add backend/app/core/migrations.py backend/app/migrations/versions/20260713_0018_human_review_core.py backend/tests/test_human_review_migration_0018.py
git commit -m "feat: add reversible human review core migration"
```

## Task 6: Revisión 0019 de proyección humana

**Files:**
- Create: `backend/app/migrations/versions/20260713_0019_human_review_projection.py`
- Modify: `backend/app/core/migrations.py`
- Create: `backend/tests/test_human_review_migration_0019.py`

**Interfaces:** Mismo contrato; `down_revision="20260713_0018_human_review_core"`.

- [ ] **Step 1: Escribir RED PostgreSQL**

Probar identidad única, alias activo único, dos alias iguales no pueden apuntar a identidades distintas, override activo único por contexto, field path raw rechazado por DB, self-FK de supersession y downgrade simétrico.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_migration_0019 -v
```

Expected: FAIL por revisión 0019 ausente; cero skips.

- [ ] **Step 3: Implementar la revisión**

Metadatos:

```python
VERSION = "20260713_0019_human_review_projection"
revision = VERSION
down_revision = "20260713_0018_human_review_core"
```

Crear las tres tablas de 4.4–4.6 y sus constraints exactos, incluido `field_overrides.stable_target_key VARCHAR(128)`. La allowlist SQL de `field_path` debe coincidir byte por byte con `OverrideField`. Downgrade falla si existen decisiones humanas en una base no descartable; el script de ciclo pasa una conexión marcada `application_name=b2b1_disposable_migration_test` y elimina en orden overrides, aliases, identities.

- [ ] **Step 4: Registrar y ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_migration_0019 -v
```

Expected: PASS; los intentos `raw_name`, `raw_author_name`, `person_key` y `parsed_payload` fallan por check constraint.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que alias no puede almacenar herencia científica. Commit:

```powershell
git add backend/app/core/migrations.py backend/app/migrations/versions/20260713_0019_human_review_projection.py backend/tests/test_human_review_migration_0019.py
git commit -m "feat: add reversible human projection migration"
```

## Task 7: Revisión 0020 del ledger inmutable

**Files:**
- Create: `backend/app/migrations/versions/20260713_0020_human_review_audit.py`
- Modify: `backend/app/core/migrations.py`
- Create: `backend/tests/test_human_review_migration_0020.py`

**Interfaces:** Mismo contrato; `down_revision="20260713_0019_human_review_projection"` y head B2B.1 exacto.

- [ ] **Step 1: Escribir RED PostgreSQL**

Probar columnas, checks hash/payload, evento correctivo relacionado, unique hash, índices, trigger append-only, retención sin función de purge y ciclo completo 0017→0020.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_migration_0020 -v
```

Expected: FAIL por revisión 0020 ausente; cero skips.

- [ ] **Step 3: Implementar revisión y validación**

Metadatos:

```python
VERSION = "20260713_0020_human_review_audit"
revision = VERSION
down_revision = "20260713_0019_human_review_projection"
```

Crear `audit_events`, checks e índices; reutilizar `b2b_reject_append_only_mutation()`. `assert_schema` verifica trigger habilitado (`tgenabled='O'`) y todos los índices. Downgrade elimina trigger y tabla, sin tocar decisiones.

- [ ] **Step 4: Registrar y ejecutar GREEN de las cuatro revisiones**

```powershell
python -m unittest tests.test_human_review_migration_0017 tests.test_human_review_migration_0018 tests.test_human_review_migration_0019 tests.test_human_review_migration_0020 -v
```

Expected: PASS; registry head `20260713_0020_human_review_audit`, 20 versiones únicas.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que el runner registra cada versión solo después de que `upgrade()` y `assert_schema()` terminen. Commit:

```powershell
git add backend/app/core/migrations.py backend/app/migrations/versions/20260713_0020_human_review_audit.py backend/tests/test_human_review_migration_0020.py
git commit -m "feat: add immutable b2b1 audit migration"
```

## Task 8: Separación owner/app y permisos SQL

**Files:**
- Modify: `backend/app/core/config.py:5-24`
- Modify: `backend/app/core/database.py:1-17`
- Modify: `backend/app/main.py:8-30`
- Modify: `docker-compose.yml:27-45`
- Create: `backend/scripts/configure_human_review_privileges.py`
- Create: `backend/tests/test_human_review_privileges.py`

**Interfaces:** `settings.migration_database_url`; `migration_engine`; CLI de grants/revokes. No cambia routers.

- [ ] **Step 1: Escribir RED para engines distintos y privilegios**

Crear en PostgreSQL un rol temporal `b2b1_app_<uuid>` no-superuser. Probar que el owner migra; el app puede SELECT/INSERT, no puede UPDATE/DELETE/TRUNCATE en `review_decisions` ni `audit_events`, y no posee tablas ni schema.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_privileges -v
```

Expected: FAIL porque no existe separación ni configurador; cero skips.

- [ ] **Step 3: Implementar separación mínima**

`config.py` añade `migration_database_url: str | None = None`. `database.py` mantiene `engine` desde `database_url` y construye `migration_engine` desde `migration_database_url or database_url`. `main.py` pasa únicamente `migration_engine` a `run_migrations`. Docker Compose exige `APP_DATABASE_URL` y `MIGRATION_DATABASE_URL` para backend; no almacena nuevos secretos.

El configurador valida nombre de rol con `^[A-Za-z_][A-Za-z0-9_]{0,62}$`, exige rol existente, `rolsuper=false`, `rolcreatedb=false`, `rolcreaterole=false`, concede acceso operativo a tablas existentes y revoca UPDATE/DELETE/TRUNCATE de las dos tablas append-only. Al final consulta `has_table_privilege` y falla si alguna prohibición no es efectiva.

- [ ] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_privileges -v
```

Expected: PASS; la excepción SQL es `InsufficientPrivilege` para DML destructivo.

- [ ] **Step 5: Revisión y checkpoint**

Confirmar que la cuenta app real deberá ser distinta del owner/superusuario antes del apply. Commit:

```powershell
git add backend/app/core/config.py backend/app/core/database.py backend/app/main.py docker-compose.yml backend/scripts/configure_human_review_privileges.py backend/tests/test_human_review_privileges.py
git commit -m "security: separate migration and application database roles"
```

## Task 9: Hash encadenado y correcciones de auditoría

**Files:**
- Create: `backend/app/services/human_review_audit.py`
- Create: `backend/tests/test_human_review_audit.py`

**Interfaces:** Produce `compute_audit_event_hash`, `append_audit_event` y `append_audit_correction`.

- [x] **Step 1: Escribir RED**

Probar hash determinista, cambio de hash ante cualquier campo, encadenamiento por agregado, rechazo si `previous_event_id` o `previous_event_hash` no coinciden, corrección con `corrects_event_id`, y ausencia de update/delete. En PostgreSQL 16 descartable, usar sesiones realmente concurrentes para demostrar espera y cadena lineal en un mismo agregado, independencia entre agregados y liberación automática del lock tras rollback.

- [x] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_audit -v
```

Expected: FAIL por servicio ausente.

- [x] **Step 3: Implementar append en una transacción**

La sustitución arquitectónica autorizada usa un advisory lock transaccional por agregado y no exige privilegio `UPDATE` sobre `audit_events`:

```python
material = json.dumps(
    ["b2b:audit-lock:v1", aggregate_type, aggregate_key],
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
digest = sha256(material).digest()
lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
```

Dentro de la transacción del `Session`, ejecutar `SELECT pg_advisory_xact_lock(:lock_key)` antes de consultar el head con un `SELECT` normal ordenado por `occurred_at DESC, id DESC`. Validar el payload discriminado y versionado, comprobar `previous_event_id`, `previous_event_hash` y el hash almacenado del head, calcular SHA-256 sobre el envelope canónico sin `event_hash`, insertar exactamente una fila y hacer `flush` sin commit interno. `append_audit_correction` carga el original inmutable, adquiere el mismo lock por agregado, enlaza contra el head vigente y crea `audit_corrected`; nunca altera la fila original.

La clave es estable entre procesos porque deriva de JSON UTF-8 canónico, SHA-256 y conversión explícita a `bigint` firmado. Una colisión teórica de 64 bits solo añade serialización entre agregados distintos; no permite concurrencia dentro del mismo agregado. El lock se libera automáticamente en commit o rollback. No se crea tabla de locks, migración 0021 ni función SQL adicional.

- [x] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_audit -v
```

Expected: PASS en unitarias y PostgreSQL. Evidencia focal: `backend/reports/human_review_b2b1_20260713T142539-0500/checkpoints/task-09-final/logs/green-final-task9.txt`, 14/14 PASS con concurrencia real, rollback y matriz app intacta.

- [x] **Step 5: Revisión y checkpoint**

Confirmar que no hay endpoint ni job de borrado. Revisión independiente final: `SPEC PASS / QUALITY PASS`, sin hallazgos Critical, Important o Minor. Commit:

```powershell
git add backend/app/services/human_review_audit.py backend/tests/test_human_review_audit.py
git commit -m "feat: add chained immutable b2b1 audit events"
```

## Task 10: Autorización deny-by-default y asignación explícita

**Files:**
- Create: `backend/app/services/human_review_authorization.py`
- Create: `backend/app/services/human_review_capabilities.py`
- Create: `backend/scripts/assign_b2b_capabilities.py`
- Create: `backend/tests/test_human_review_authorization.py`

**Interfaces:** Produce policy base, dry-run/apply de manifiesto y CLI. Consume auditoría de Tarea 9.

- [ ] **Step 1: Escribir RED de matriz negativa y manifiesto**

Probar: sin fila activa deniega; Career deniega aunque exista fila corrupta; Research Manager permite acciones científicas base; System Admin solo lectura/auditoría/soporte y no propone/aplica/revierte ciencia; `FACULTY_ADMIN` sin asignación deniega; ningún seed recibe capacidad; manifiesto con email/user id discordantes falla; segunda aplicación no inserta.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_authorization -v
```

Expected: FAIL por servicios y script ausentes.

- [ ] **Step 3: Implementar policy y contratos de cuenta**

`ApprovedAccountAssignmentV1` contiene `user_id`, `email`, `capability`; `ApprovedAccountAssignmentsV1` contiene `schema_version=1`, `approval_reference` y lista no vacía. La policy base exacta es:

- RESEARCH_MANAGER: `view_foundations`, `view_audit`, `apply_scientific`, `propose_scientific`, `revert_scientific`.
- SYSTEM_ADMIN: `view_foundations`, `view_audit`, `manage_technical_access`.
- ausencia, Career o cualquier acción no listada: denegada.

`plan_capability_assignments` reconsulta usuario activo por ID y email exactos, no cambia `users.role`, rechaza Career y reporta `insert|unchanged|conflict`. Apply exige hash exacto del archivo, inserta solo `insert`, produce evento `capability_assigned` y revierte toda la transacción ante conflicto.

- [ ] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_authorization -v
```

Expected: PASS; Admin obtiene `False` para las tres acciones científicas.

- [ ] **Step 5: Revisión y checkpoint**

Buscar que no haya strings de correos en los nuevos archivos de producción/prueba. Commit:

```powershell
git add backend/app/services/human_review_authorization.py backend/app/services/human_review_capabilities.py backend/scripts/assign_b2b_capabilities.py backend/tests/test_human_review_authorization.py
git commit -m "feat: enforce explicit deny by default b2b access"
```

## Task 11A: Cierre de contratos y snapshot restaurable

**Files:**
- Modify: `backend/app/schemas/human_review.py`
- Modify: `backend/app/schemas/human_review_operations.py`
- Modify: `backend/tests/test_human_review_contracts.py`
- Create: `backend/tests/test_human_review_projection_contracts.py`

**Interfaces:** materializa `HumanIdentityProjectionV1`, `FunctionalReversalCommandV1`, `OptimisticLockError` y los cuatro snapshots cerrados. No crea servicios, tablas, columnas ni migraciones.

- [x] **Step 1: RED contractual**

Probar contratos ausentes, campos prohibidos, matriz scope/context, pares incompletos, reversión sin snapshot, orden/deduplicación canónicos, serialización repetible y prohibición de snapshots enviados en el comando.

- [x] **Step 2: Persistir el contrato dentro del JSONB existente**

Extender payloads proyectables con el par opcional `projection_before`/`projection_after` y exigirlo en `DecisionReversalPayloadV1`. Una decisión aprobada que bloquee proyección deberá exigir ambos en Task 11. La única fuente futura de restauración es `decision_id_to_revert.payload.projection_before`; no se reconstruye desde overrides, punteros, secuencias o timestamps.

- [x] **Step 3: Compatibilidad y frontera**

Los payloads no reversibles sin snapshot continúan validándose. Una decisión sin snapshot completo no se puede revertir. No se modifican ORM ni migraciones 0017–0020 y no se inicia implementación funcional de Task 11.

## Task 11: Estados, proyección fundacional y reversión append-only

**Files:**
- Create: `backend/app/services/human_review_state.py`
- Create: `backend/app/services/human_review_projection.py`
- Create: `backend/tests/test_human_review_projection.py`

**Interfaces:** Produce transición, versión, lectura de identidad, puntero vigente y reversión. Consume autorización y auditoría; no toca B2A.

- [ ] **Step 1: Escribir RED**

Probar cada transición aprobada de la especificación y cada transición inválida; dos updates con la misma versión, donde el segundo falla; current decision solo apunta a decisión del mismo caso; decisión approved bloqueada; alias resuelve solo identidad; payload de merge/separate no traslada roles/productos/KPI; reversión agrega decisión/evento/overrides y conserva filas anteriores.

- [ ] **Step 2: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_projection -v
```

Expected: FAIL por servicios ausentes.

- [ ] **Step 3: Implementar transición y compare-and-swap**

`assert_case_transition` usa exactamente el grafo de la sección 6.2 de la especificación. `set_current_decision` ejecuta un UPDATE condicionado por `id` y `version=expected_version`, incrementa `version` y falla con `OptimisticLockError` si rowcount no es 1.

`load_identity_projection` retorna `HumanIdentityProjectionV1(canonical_identity_key, canonical_name, decision_id, locked, aliases)` desde el current decision y overrides activos. No consulta ni copia roles/productos.

`append_functional_reversal` exige `RESEARCH_MANAGER`, expected version y decisión previa del caso. Valida tipadamente `decision_id_to_revert.payload`, toma exclusivamente su `projection_before`, compara `restore_decision_id` con `projection_before.current_decision_id` cuando esté presente y rechaza decisiones sin snapshot completo. Inserta `ReviewDecision(type=reverted,lifecycle=approved)`, crea overrides desde ese snapshot persistido, supersede proyección vigente, mueve current pointer y agrega `functional_reversion`. No reconstruye estado desde overrides históricos, `superseded_by_id`, punteros, secuencias, timestamps o estado actual. Todo ocurre en una transacción; nunca actualiza decisiones/auditoría.

- [ ] **Step 4: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_projection -v
```

Expected: PASS, incluido conflicto optimista PostgreSQL.

- [ ] **Step 5: Confirmar frontera B2B.1 y checkpoint**

`rg -n "human_review" backend/app/services/validated_read_service.py backend/app/api frontend` no debe mostrar nuevas referencias. Commit:

```powershell
git add backend/app/services/human_review_state.py backend/app/services/human_review_projection.py backend/tests/test_human_review_projection.py
git commit -m "feat: add b2b1 projection and append only reversal"
```

## Task 12: Backfill dry-run y apply idempotente

**Files:**
- Create: `backend/app/services/human_review_backfill.py`
- Create: `backend/scripts/backfill_human_review_b2b1.py`
- Create: `backend/tests/test_human_review_backfill.py`

**Interfaces:** Produce descubrimiento, plan y apply. Consume B1/B2A solo en lectura, stable targets, invariantes, proyección y auditoría.

- [x] **Step 0: Cerrar contratos, matriz y snapshots con RED/GREEN**

Crear RED para los contratos cerrados de backfill y probar cierre/frozen/versionado, orden canónico, duplicados, conteos, matriz población/fuente/case type y snapshots UUIDv5 exactos. Materializar los contratos en `human_review_operations.py` y ejecutar GREEN contractual antes de crear el servicio.

- [x] **Step 1: Escribir RED de poblaciones y solapamiento**

Fixtures PostgreSQL representan un mismo target presente en identidad pendiente, producto pendiente, director pendiente y externo pendiente. Probar que el reporte conserva cuatro membresías pero no afirma cuatro personas/casos independientes; deduplica por `(case_type,stable_target_key)`; posible match crea caso informativo, no decisión; un target sin documento/localizador estable hace fallar dry-run.

- [x] **Step 2: Escribir RED de locks e idempotencia**

Probar que `identity_locked=true` con canonical key/name crea caso resolved, decisión approved preservando actor/fecha, `projection_before` y `projection_after` completos, identidad, alias global de persona compatible, dos overrides locales bloqueados y eventos; un lock incompleto queda reportado como blocker; segunda ejecución inserta cero; error intermedio revierte todo.

- [x] **Step 3: Ejecutar RED**

```powershell
python -m unittest tests.test_human_review_backfill -v
```

Expected: FAIL por servicio/script ausentes; cero skips.

- [x] **Step 4: Implementar el descubrimiento exacto**

Usar `ValidatedReadService.canonical_participants()`, `production_views(visibility="pending")`, `entity_list()` + `project_director_state()` como clasificadores de solo lectura, y reconsultar las filas ORM para localizadores. No modificar esos métodos.

El plan informa por separado:

- filas canónicas pendientes actuales;
- productos pendientes actuales;
- relaciones director-proyecto pendientes actuales;
- externos pendientes actuales;
- intersecciones entre poblaciones por target estable;
- casos únicos por tipo + stable key;
- locks importables y blockers;
- coincidencias informativas B2A.1 sin decisión.

No contiene una suma `53+17+7+4`; reporta cada conteo fuente y la unión real calculada.

- [x] **Step 5: Implementar apply protegido**

El CLI dry-run usa `DATABASE_URL`, verifica que el rol conectado sea least-privilege con la matriz exacta B2B/fuentes y abre una transacción read-only. Siempre realiza cero escrituras B2B; si existen blockers, guarda el plan diagnóstico después del rollback y termina con error. Apply inicia una única transacción con `MIGRATION_DATABASE_URL`, verifica owner/app y que app carece de `INSERT/UPDATE/DELETE/TRUNCATE` sobre fuentes, toma `pg_advisory_xact_lock(hashtext('human-review-b2b1-backfill'))` y adquiere, en orden estable, `LOCK TABLE external_researchers, person_roles, research_entities, research_projects, scientific_production_authors, scientific_productions IN SHARE MODE`. Confirma la identidad exacta del advisory lock y seis `ShareLock`, ejecuta `SET LOCAL ROLE <B2B1_APPLICATION_DB_ROLE>` una vez y, sin `RESET ROLE`, realiza toda lectura, revalidación, inserción B2B y auditoría como app. El owner no inserta B2B y app no modifica fuentes. Apply revalida plan/hash/snapshot/backup y compara la semántica completa de cada fila existente antes de clasificarla como unchanged, incluida la cadena exacta de auditoría. Los guards incluyen nombres calificados por esquema y `ONLY`; los errores SQL se publican sanitizados.

Las pruebas PostgreSQL 16 demuestran que app no puede `FOR SHARE`, owner adquiere los seis `ShareLock`, las mutaciones fuente esperan, `SET LOCAL ROLE app` conserva los locks, app puede leer fuentes e insertar solo B2B, no puede escalar ni mutar fuentes, y commit/rollback liberan advisory/table locks. Dos applies se serializan por el advisory lock; el segundo reconstruye el plan y finaliza como no-op.

- [x] **Step 6: Ejecutar GREEN**

```powershell
python -m unittest tests.test_human_review_backfill -v
```

Expected: PASS; segunda ejecución retorna exactamente `created_review_items=0`, `created_decisions=0`, `created_overrides=0`, `created_identities=0`, `created_aliases=0`, `created_audit_events=0`.

- [x] **Step 7: Revisión y checkpoint**

Confirmar que no hay UPDATE a `person_roles`, `scientific_production_authors`, `scientific_productions`, `research_entities`, `research_projects`, `external_researchers`, jobs o trazas. Commit:

```powershell
git add backend/app/services/human_review_backfill.py backend/scripts/backfill_human_review_b2b1.py backend/tests/test_human_review_backfill.py
git commit -m "feat: add guarded idempotent b2b1 backfill"
```

## Task 13: Backup restaurable y ciclo descartable completo

**Files:**
- Create: `backend/scripts/verify_human_review_b2b1.py`
- Generate: `backend/reports/human_review_b2b1_<timestamp>/` artifacts de preflight

**Interfaces:** Consume todas las revisiones, invariantes y pruebas; no aplica a la base real.

- [ ] **Step 1: Escribir RED operacional del verificador**

`verify_human_review_b2b1.py` debe fallar si falta backup/hash/TOC, si restore no abre, si head no es 0016 antes del ciclo, si una revisión se registra sin constraint, si un test PG se omite o si downgrade deja objetos.

- [ ] **Step 2: Ejecutar RED**

```powershell
python scripts/verify_human_review_b2b1.py --database-url-env MIGRATION_DATABASE_URL --report-dir $reportDir --expected-head 20260713_0020_human_review_audit
```

Expected: FAIL con `backup artifact is required` antes de recibir las rutas.

- [ ] **Step 3: Crear backup nuevo sin redirección binaria de PowerShell**

```powershell
docker compose up -d postgres
docker compose exec -T postgres sh -lc 'pg_dump -U postgres -d science_faculty -Fc -f /tmp/b2b1-pre.dump'
docker compose cp postgres:/tmp/b2b1-pre.dump "$reportDir/backup.dump"
docker compose exec -T postgres pg_restore -U postgres --list /tmp/b2b1-pre.dump | Set-Content -Encoding UTF8 "$reportDir/backup.toc.txt"
(Get-FileHash -Algorithm SHA256 "$reportDir/backup.dump").Hash | Set-Content -Encoding ASCII "$reportDir/backup.dump.sha256"
```

Expected: dump no vacío, TOC contiene `schema_migrations`, `person_roles`, `scientific_production_authors`, `import_jobs` e `imported_ocr_traces`.

- [ ] **Step 4: Probar restore y ciclo**

```powershell
docker compose exec -T postgres dropdb -U postgres --if-exists science_faculty_b2b1_restore
docker compose exec -T postgres createdb -U postgres science_faculty_b2b1_restore
docker compose exec -T postgres pg_restore -U postgres -d science_faculty_b2b1_restore --exit-on-error /tmp/b2b1-pre.dump
$env:B2B1_TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/science_faculty_b2b1_restore'
$env:MIGRATION_DATABASE_URL=$env:B2B1_TEST_DATABASE_URL
python scripts/verify_human_review_b2b1.py --database-url-env MIGRATION_DATABASE_URL --report-dir $reportDir --expected-head 20260713_0020_human_review_audit
```

Expected: aplica 0017–0020, valida, baja 0020→0017 eliminando sus filas de `schema_migrations`, verifica B1/B2A, reaplica y termina en 0020. G2, G3 y G4 deben quedar PASS.

- [ ] **Step 5: Ejecutar suite PostgreSQL enfocada**

```powershell
python -m unittest tests.test_human_review_migration_0017 tests.test_human_review_migration_0018 tests.test_human_review_migration_0019 tests.test_human_review_migration_0020 tests.test_human_review_privileges tests.test_human_review_audit tests.test_human_review_projection tests.test_human_review_backfill -v 2>&1 | Tee-Object "$reportDir/postgres-tests.txt"
```

Expected: PASS y cero skips.

- [ ] **Step 6: GREEN, revisión y checkpoint**

Ejecutar nuevamente el verificador con todos los artefactos; Expected: `STATUS=PASS`. Commit del script, no del apply:

```powershell
git add backend/scripts/verify_human_review_b2b1.py
git commit -m "test: verify b2b1 backup and migration restore cycle"
```

## Task 14: Apply real, idempotencia y gate de cuentas

**Files:**
- Generate only: artifacts de apply en `backend/reports/human_review_b2b1_<timestamp>/`

**Interfaces:** Consume scripts ya verdes. No cambia archivos fuente.

- [ ] **Step 1: Capturar snapshot y dry-run real**

```powershell
$env:PYTHONPATH=(Resolve-Path 'backend')
Set-Location backend
python -c "from sqlalchemy import create_engine; from app.services.human_review_invariants import capture_b2b1_invariants; import os; e=create_engine(os.environ['MIGRATION_DATABASE_URL']); print(capture_b2b1_invariants(e.connect()).model_dump_json(indent=2))" | Set-Content -Encoding UTF8 "$reportDir/pre-b2b1-invariants.json"
(Get-FileHash -Algorithm SHA256 "$reportDir/pre-b2b1-invariants.json").Hash | Set-Content -Encoding ASCII "$reportDir/pre-b2b1-invariants.json.sha256"
python scripts/backfill_human_review_b2b1.py --dry-run --output "$reportDir/backfill-dry-run.json"
(Get-FileHash -Algorithm SHA256 "$reportDir/backfill-dry-run.json").Hash | Set-Content -Encoding ASCII "$reportDir/backfill-dry-run.json.sha256"
```

Expected: no escritura DB; KPI 2; blockers cero; poblaciones e intersecciones explícitas.

- [ ] **Step 2: Gate humano G5**

Un revisor compara el dry-run con la especificación, escribe en `backfill-approval.txt` el SHA-256 exacto, conteos fuente, unión real, locks importables y `APPROVED`. Sin esa línea exacta el apply no se ejecuta.

- [ ] **Step 3: Aplicar revisiones y backfill**

```powershell
$planHash=(Get-Content "$reportDir/backfill-dry-run.json.sha256").Trim()
$snapshotHash=(Get-Content "$reportDir/pre-b2b1-invariants.json.sha256").Trim()
$backupHash=(Get-Content "$reportDir/backup.dump.sha256").Trim()
python -c "from app.core.database import migration_engine; from app.core.migrations import run_migrations; print(run_migrations(migration_engine))" | Tee-Object "$reportDir/migration-real-apply.txt"
python scripts/backfill_human_review_b2b1.py --apply --plan "$reportDir/backfill-dry-run.json" --approved-plan-sha256 $planHash --snapshot "$reportDir/pre-b2b1-invariants.json" --backup "$reportDir/backup.dump" --backup-sha256 $backupHash --output "$reportDir/backfill-apply.json"
python scripts/backfill_human_review_b2b1.py --apply --plan "$reportDir/backfill-dry-run.json" --approved-plan-sha256 $planHash --snapshot "$reportDir/pre-b2b1-invariants.json" --backup "$reportDir/backup.dump" --backup-sha256 $backupHash --output "$reportDir/backfill-second-run.json"
```

Expected: head 0020; segunda salida con todos los `created_* = 0`; KPI permanece 2.

- [ ] **Step 4: Solicitar las cuentas exactas antes de asignar**

Detenerse y solicitar una lista aprobada con, para cada cuenta real: `user_id`, email exacto existente y una capacidad `RESEARCH_MANAGER` o `SYSTEM_ADMIN`, más una referencia de aprobación. No sugerir cuentas, no usar las seeds, no inferir desde `FACULTY_ADMIN` y no aplicar si falta una sola correspondencia.

- [ ] **Step 5: Preparar manifiesto sin correos ficticios y ejecutar dry-run**

Guardar el contenido aprobado en `$reportDir/approved-account-assignments.json`; validar que su hash coincide con la aprobación externa.

```powershell
python scripts/assign_b2b_capabilities.py --dry-run --manifest "$reportDir/approved-account-assignments.json" --output "$reportDir/capabilities-dry-run.json"
```

Expected: solo `insert|unchanged`, ninguna Career, ninguna discordancia ID/email y ninguna mutación de `users.role`.

- [ ] **Step 6: Configurar cuenta SQL app y probar privilegios antes de asignar**

La cuenta se crea/intercambia mediante secreto operativo, no se guarda contraseña en el repositorio. Definir `B2B1_APPLICATION_DB_ROLE`, `APP_DATABASE_URL` y `MIGRATION_DATABASE_URL`, comprobar que los usuarios DB son distintos y ejecutar:

```powershell
python scripts/configure_human_review_privileges.py --owner-url-env MIGRATION_DATABASE_URL --application-role-env B2B1_APPLICATION_DB_ROLE --apply 2>&1 | Tee-Object "$reportDir/privilege-proof.txt"
```

Expected: app no-superuser; `UPDATE/DELETE/TRUNCATE=false` para decisions/audit.

- [ ] **Step 7: Apply de cuentas con hash aprobado**

```powershell
$accountsHash=(Get-FileHash -Algorithm SHA256 "$reportDir/approved-account-assignments.json").Hash
python scripts/assign_b2b_capabilities.py --apply --manifest "$reportDir/approved-account-assignments.json" --approved-manifest-sha256 $accountsHash --operator-identifier $env:B2B1_OPERATOR_IDENTIFIER --output "$reportDir/capabilities-apply.json"
```

Expected: asignaciones exactas, auditoría correlacionada, cero Career y roles existentes intactos. Si aún no se entregaron cuentas, B2B.1 queda detenido en este gate sin revertir el esquema/backfill ya verificado y sin afirmar cierre.

- [ ] **Step 8: Estrategia de rollback real**

Antes de decisiones: en base descartable se permite downgrade 0020→0017. Después de importar cualquier lock humano, la base real no ejecuta downgrade destructivo. La reversión normal agrega decisiones `reverted`; un rollback de aplicación deja las tablas aditivas intactas. Para eliminar esquema se detienen escrituras, se exporta ledger y se restaura el backup completo probado, aceptando explícitamente la pérdida de toda escritura posterior al backup. Registrar la ruta elegida en el informe.

## Task 15: Verificación completa, revisión independiente y entrega

**Files:**
- Generate: artefactos finales y `backend/reports/human_review_b2b1_<timestamp>/b2b1-delivery-report.md`

**Interfaces:** No produce capacidad funcional nueva; certifica el cierre y detiene antes de B2B.2.

- [ ] **Step 1: Ejecutar todas las integraciones PostgreSQL sin skips**

```powershell
$env:CANONICAL_IDENTITY_TEST_DATABASE_URL=$env:B2B1_TEST_DATABASE_URL
$env:CANONICAL_OPERATIONAL_TEST_DATABASE_URL=$env:B2B1_TEST_DATABASE_URL
$env:DROPBOX_VERSIONING_TEST_DATABASE_URL=$env:B2B1_TEST_DATABASE_URL
python -m unittest discover -s tests -p 'test_*.py' -v 2>&1 | Tee-Object "$reportDir/full-suite.txt"
```

Expected: exit 0; las suites B2B.1 PostgreSQL no muestran `skipped`.

- [ ] **Step 2: Compilar todo el backend**

```powershell
python -m compileall app scripts tests 2>&1 | Tee-Object "$reportDir/compileall.txt"
```

Expected: exit 0 y ningún `*** Error compiling`.

- [ ] **Step 3: Repetir invariantes y gates**

Capturar snapshot posterior, comparar con el previo y escribir `immutable-comparison.json`; consultar KPI=2; verificar cadena 0020; probar permisos app; consultar duplicados activos, aliases y decisiones/eventos. Cualquier diferencia en G1–G12 detiene el cierre.

- [ ] **Step 4: Verificar alcance de archivos**

Con Git:

```powershell
git diff --name-only HEAD~1
```

Sin Git, comparar `inventory-before.csv`/hashes con el inventario final. Deben aparecer solo rutas de la sección 3 y artefactos del reporte. Confirmar cero cambios en frontend, API, parser, Dropbox, `entities.py`, `enums.py`, `ValidatedReadService` e `init_db.py`.

- [ ] **Step 5: Revisión independiente Spec PASS**

Un revisor distinto mapea cada requisito de la matriz de la sección 10 a código, prueba y evidencia; escribe `independent-spec-review.md`. Cualquier requisito sin evidencia es FAIL.

- [ ] **Step 6: Revisión independiente Quality PASS**

Otro pase revisa transacciones, locks, constraints, hashes, permisos, rollback, manejo de secretos, idempotencia y claridad de errores; escribe `independent-quality-review.md`. Resolver findings importantes y repetir pruebas afectadas.

- [ ] **Step 7: Escribir informe y detenerse**

El informe incluye versiones 0017–0020, backup/restore, dry-run/apply/segunda corrida, cuentas asignadas por ID y capacidad sin exponer secretos, permisos, invariantes, KPI, tests, hashes, limitaciones y ambos PASS. Commit si Git existe:

```powershell
git add backend/reports/human_review_b2b1_*/
git commit -m "docs: deliver verified b2b1 foundations"
```

No crear rama/PR si Git no existe y no iniciar B2B.2.

---

## 9. Estrategia de pruebas consolidada

| Nivel | Casos exactos |
|---|---|
| Unitario | enums, payload extra forbid, stable key, raw allowlist, hash canónico, transiciones, matriz base |
| ORM | columnas, tipos, FKs, versiones, alias sin campos científicos |
| PostgreSQL | constraints, partial uniques, FK diferible, triggers, app privileges, optimistic update, append-only |
| Migraciones | G1, no registro en fallo, upgrade/downgrade/re-upgrade 0017–0020 |
| Backfill | dry-run read-only, overlap, target estable, lock import, transaction rollback, segunda corrida cero |
| Reversión | nueva decisión/evento, historia intacta, current pointer actualizado, Admin rechazado |
| Invariantes | hashes raw/trazas/payloads/jobs/ciencia, B1 locks, B2A contracts, KPI 2 |
| Suite | todas las pruebas existentes, compileall, cero skips B2B.1 PostgreSQL |

## 10. Matriz requisito → tarea → prueba/gate

| Requisito aprobado B2B.1 | Tarea | Prueba o gate |
|---|---:|---|
| RESEARCH_MANAGER / SYSTEM_ADMIN separados de roles previos | 4, 10 | autorización + G9/G10 |
| CAREER_MANAGER sin B2B | 4, 10, 14 | trigger, matriz negativa, G9 |
| deny-by-default y cuentas explícitas | 10, 14 | manifiesto/hash + gate de cuentas |
| review_items | 3, 5 | ORM + migración 0018 |
| review_decisions append-only | 3, 5, 8 | trigger + privilege G11 |
| field_overrides | 3, 6 | migración 0019 + proyección |
| canonical_identities | 3, 6, 11 | constraints + lectura interna |
| person_aliases solo identidad | 3, 6, 11 | G12 |
| audit_events append-only e indefinidos | 3, 7, 8, 9 | trigger, grants, hash chain |
| review_action_batches no indispensable | mapa de alcance | ausencia verificada G0 |
| tipos/estados/scopes cerrados | 1, 5, 6 | contratos + checks DB |
| payloads tipados/versionados | 1, 5, 7 | Pydantic + JSONB checks |
| versionado optimista | 3, 5, 11 | compare-and-swap PG |
| raw nunca target | 1, 6 | rechazo Pydantic + DB |
| cadena real y siguiente identificador | 4 | G1 |
| revisiones aditivas y reversibles | 4–7, 13 | G4 |
| no registrar revisión incompleta | 4–7 | tests de `assert_schema` |
| backup/restore probado | 13 | G2 |
| dry-run obligatorio | 12, 14 | G5 |
| poblaciones solapadas | 12 | overlap fixture + reporte |
| un caso activo por tipo+target | 5, 12 | partial unique + idempotencia |
| matches informativos no son decisiones | 12 | assertion decisions=0 |
| importar identity_locked con actor/fecha | 12 | fixture lock completo |
| segunda ejecución cero | 12, 14 | G6 |
| no cambiar status/KPI/raw/jobs/trazas/payloads | 2, 12, 15 | G7/G8 |
| permisos SQL append-only | 8 | G11 |
| correcciones por eventos nuevos | 9 | correction test |
| hash/correlación | 7, 9 | hash chain test |
| identidad/alias global y overrides locales | 1, 6, 11 | projection tests |
| decisiones bloqueadas/current pointer | 5, 11 | PG + optimistic version |
| no conectar todavía lectores B2A/UI | 11, 15 | scope scan G0 |
| reversión funcional append-only | 11, 14 | reversal test + estrategia real |
| fusión/separación solo modelo | 1, 11 | payload tests sin apply real |
| PostgreSQL real/constraints | 4–13 | G3 |
| compileall/suite completa | 15 | logs finales |
| informe + Spec PASS/Quality PASS | 15 | criterios de cierre |

## 11. Riesgos y mitigaciones del plan

| Riesgo | Mitigación ejecutable |
|---|---|
| Nueva revisión ocupa 0017–0020 antes de ejecutar | G1 detiene y exige replanificación |
| App sigue usando superusuario | separación de engines + G11 antes del cierre |
| Enum de `users.role` cambia permisos existentes | capacidad separada; `users.role` intacto |
| Target depende de surrogate recreable | surrogate excluido del hash; documento/localizador/raw hash obligatorios |
| Conteos fuente se suman incorrectamente | memberships + intersecciones + unión real en dry-run |
| Lock legado incompleto produce decisión inventada | blocker explícito; apply se detiene |
| Alias copia ciencia | esquema sin columnas y G12 |
| JSON permite forma inesperada | payload schema/version + discriminated union + extra forbid |
| Evento/decisión se edita por error | trigger y permisos SQL |
| Migration DDL queda parcial | una transacción + `assert_schema` antes del registro |
| Downgrade destruye decisiones reales | solo ciclo descartable; base real conserva esquema o restaura backup completo |
| Backfill compite con reprocesamiento | advisory lock + source locks + invariant recheck |
| Git ausente oculta alcance | inventario, copias, SHA-256, logs y revisión independiente por tarea |

## 12. Criterios de cierre exigidos

B2B.1 solo puede declararse cerrado cuando todos son verdaderos:

- [ ] revisiones 0017–0020 aplicadas y registradas en orden;
- [ ] upgrade/downgrade/re-upgrade probado en restore descartable;
- [ ] backup custom-format restaurable y restore probado;
- [ ] backfill dry-run aprobado por hash;
- [ ] apply transaccional y segunda ejecución con cero inserciones;
- [ ] decisiones y overrides fuera de tablas reconstruibles;
- [ ] auditoría y decisiones append-only protegidas por trigger y permisos;
- [ ] capacidades deny-by-default y cuentas reales asignadas solo desde lista aprobada;
- [ ] CAREER_MANAGER sin acceso B2B;
- [ ] SYSTEM_ADMIN sin autoridad científica base;
- [ ] stable targets deterministas y caso activo único;
- [ ] B1/B2A, raw, jobs, trazas, payloads y ciencia intactos;
- [ ] KPI de productos elegibles igual a 2;
- [ ] sin endpoints, botones, bandejas ni UI funcional;
- [ ] suite completa y compileall verdes;
- [ ] informe de entrega con hashes y artefactos;
- [ ] revisión independiente `Spec PASS` y `Quality PASS`;
- [ ] ejecución detenida antes de B2B.2.

La herramienta antimalware pertenece a B2B.4 y no participa en ningún gate B2B.1.
