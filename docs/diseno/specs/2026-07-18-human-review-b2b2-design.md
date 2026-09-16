# B2B.2 — diseño de la bandeja operativa de revisión humana

## 1. Contexto e inventario confirmado

B2B.1 está cerrado en PostgreSQL 16.13, en `20260713_0020_human_review_audit`. Hay 79 `review_items` pendientes, 80 `audit_events`, una capacidad activa para `user_id=1` (`RESEARCH_MANAGER`) y cero decisiones, overrides, identidades o aliases humanos. El KPI base de productos elegibles es 2. No existe migración 0021 ni router, endpoint o UI B2B.2.

La base existente es suficiente para persistir la decisión, la proyección, overrides e identidad/alias: `review_items`, `review_decisions`, `field_overrides`, `canonical_identities`, `person_aliases`, `audit_events` y `user_b2b_capabilities`. Los contratos B2B.1 ya definen versiones, `FunctionalReversalCommandV1`, snapshots de proyección tipados, estados, capacidades y auditoría encadenada; la taxonomía de auditoría requiere la excepción/gate 0021 documentada en §8. `HumanReviewStateService` admite los estados existentes `pending`, `in_review`, `awaiting_gestor_approval`, `resolved`, `reopened`, `conflicted`, `superseded`; no se crean estados nuevos.

La API actual usa `/api/v1`, routers por recurso, JWT mediante `get_current_user` y servicios síncronos SQLAlchemy. El frontend es Next.js 14 App Router, Tailwind, componentes propios (`AppShell`, `DataTable`), `api.ts`, `types.ts`, `useCachedQuery` y filtros globales. No tiene biblioteca de formularios, diálogo, consulta remota ni control B2B de capacidades. `EvidenceService` actual solo sube archivos, por lo que no se reutiliza para entrega B2B.2.

`ValidatedReadService` y `KpiService` actualmente leen las columnas reconstruibles (`validation_status`, identidad canónica, etc.), no las proyecciones B2B.1. Por ello B2B.2 debe añadir una capa de preferencia de proyección humana en los lectores afectados; sin ello, una decisión se almacenaría correctamente pero no sería visible inmediatamente en vistas/KPI.

## 2. Objetivo, alcance y exclusiones

La bandeja permite al Gestor de Investigación listar, buscar, filtrar, inspeccionar evidencia existente y decidir casos; aplicar correcciones/links/descartes, revertir decisiones y consultar auditoría. Una confirmación genera una sola transacción: valida capacidad y CAS, agrega una decisión append-only, materializa proyección, actualiza el caso, deja disponible el resultado a lectores/KPI y agrega auditoría. La transacción se confirma una vez al final.

La “propuesta” no es una entidad persistida. Es una vista previa local del formulario; cancelar, recargar o cerrar la descarta. No hay `POST /proposals`, tabla de borradores, reservas, ownership, expiración ni colaboración. Delegaciones, evidencia nueva, carga manual, cuarentena, exportaciones, conflictos de reprocesamiento y administración técnica quedan en B2B.3–B2B.6.

## 3. Arquitectura recomendada y alternativas

Se recomienda la **opción B: capa explícita de comandos y consultas B2B.2**.

| Opción | Diseño | Ventajas | Riesgo / descarte |
|---|---|---|---|
| A | Endpoints delgados invocan directamente `human_review_state`, `projection` y `audit`. | Pocos archivos iniciales. | Acopla HTTP a transacciones y payloads B2B.1; duplica validaciones y complica pruebas de concurrencia. |
| B (recomendada) | `HumanReviewQueryService`, `HumanReviewCommandService`, schemas HTTP y router; los comandos orquestan servicios B2B.1. | Fronteras claras, pruebas unitarias y PostgreSQL precisas, un único lugar para autorización/CAS/proyección/auditoría; prepara B2B.3+ sin añadir sus funciones. | Añade una capa y mapeos explícitos. |
| C | Servicio de aplicación único para consulta, comandos, evidencia y KPI. | Un punto de entrada. | Archivo de alta cohesión accidental; consultas/evidencia y escrituras evolucionan distinto. |

La opción B crea, al implementarse, `backend/app/services/human_review_commands.py`, `human_review_queries.py`, schemas API y `backend/app/api/v1/endpoints/human_review.py`; registra el router con prefijo `/human-review`. En frontend crea rutas y componentes acotados a `frontend/app/human-review/**` y `frontend/components/human-review/**`. No cambia el patrón visual global.

## 4. Modelo funcional y estados

`review_item` permanece `pending` hasta que un usuario abre el detalle; esa lectura no escribe ni reserva. Se decide explícitamente añadir en B2B.2 la transición directa **`pending → resolved`** a `_ALLOWED_TRANSITIONS` de `backend/app/services/human_review_state.py`, sin crear estado. Justificación: el único decisor científico B2B.2 es el Gestor y la vista previa no es persistente; forzar `in_review` generaría una escritura/versión intermedia que aparenta una reserva, funcionalmente fuera de alcance. `set_current_decision` conserva un único CAS y un único incremento de versión al confirmar. Deben añadirse tests unitarios de transición y PostgreSQL que demuestren una sola versión incrementada, una decisión y un evento por confirmación. Revertir transita `resolved → reopened → in_review/resolved` según el snapshot restaurado; si el snapshot previo era pendiente, termina en `reopened` para hacer explícita la reapertura. Un conflicto CAS no altera ningún estado: responde 409 y la UI recarga. `conflicted` queda reservado a un comando futuro que detecte contradicción funcional real; B2B.2 no lo usa como sinónimo de 409.

La decisión final es `approved`, `locks_projection=true`; sus tipos son los ya definidos. La vista previa no crea `proposed`. Una corrección, vínculo, descarte o reversión crea una fila nueva con `previous_decision_id`/`corrects_decision_id` según corresponda. Una reversión genera `decision_type=reverted`, conserva la fila revertida y restaura el snapshot precedente. La proyección se reconstruye sincrónicamente desde `projection_after`; los overrides anteriores se superseden, no se editan destructivamente. Evidencia es solo de lectura: `available`, `unavailable` o `not_recorded`, derivados de las fuentes existentes, no un estado nuevo persistido. La auditoría es append-only y encadenada por `stable_target_key`.

## 5. Matriz de casos y acciones

Todas las filas muestran: identificador/estado/prioridad, dato detectado, normalizado y canónico cuando exista, fuente/documento/página/sección/locator, confianza disponible, memberships del backfill, versión y posible impacto KPI.

| Caso | Decisiones y vínculos permitidos | Overrides permitidos | Validación / efecto / KPI |
|---|---|---|---|
| `person_identity` | validar, corregir, vincular/crear identidad global, separar, mantener separado, descartar, revertir. Vincula `CanonicalIdentity` y alias. | `canonical_identity_key`, `canonical_name`, `scientific_status`; global solo identidad/nombre, o record/document/period. | Alias normalizado único; identidad compatible. Actualiza identidad visible en participaciones; KPI de participantes si cambia elegibilidad. |
| `author_identity` | igual a persona; vínculo con identidad canónica global. | `author_identity_key`, `canonical_name`, `scientific_status`. | No aplica herencia al producto fuera del autor/caso. Puede alterar autorías/participantes y métricas derivadas. |
| `product` | validar, corregir, rechazar/descartar, revertir; no vinculación de identidad como acción principal. | `product_title`, `scientific_status` con scope record/document/period. | Título no vacío; estado científico coherente. Al validar/descartar puede cambiar productos elegibles, pendientes y descartados. |
| `project_director_relation` | validar, corregir, vincular identidad del director, mantener/separar, descartar, revertir. | `project_director_identity_key`, `project_director_relationship_status`, `scientific_status`; relación requiere `relationship_key`. | La relación es local al caso; no propaga dirección a otros proyectos. Puede cambiar elegibilidad de entidad/proyecto, no transferencia automática de KPI. |
| `external_identity` | validar, corregir, vincular/crear identidad externa global, mantener/separar, descartar, revertir. | `external_identity_key`, `external_institution`, `scientific_status`. | Institución no vacía si se corrige; identidad tipo externa o no clasificada. Cambia métricas externas/KPI cuando corresponda. |
| `possible_duplicate` | merge, mantener separado, separar, vincular si el par es compatible, descartar, revertir. | Ninguno salvo `scientific_status` record si el payload justifica el efecto. | Requiere dos referencias distintas, sin self-link; no declara impacto KPI directo. |

Acciones `approve` y `correct` se materializan como `validated`/`corrected`; `link` como `linked`/`merged`/`separated`/`maintained_separate`; `discard` como `discarded`; `revert` como `reverted`. El backend rechaza pares case/action no listados. Todas generan decisión y evento de auditoría excepto la previsualización. Motivo es obligatorio para corregir, descartar, revertir, merge/separate y cuando el valor final difiera del normalizado; validación simple puede omitirlo.

## 6. Autorización

La dependencia B2B.2 resuelve usuario JWT y llama `authorize_b2b_action`; no infiere capacidad desde `users.role`. Deny-by-default: `CAREER_MANAGER`, usuarios sin asignación y `SYSTEM_ADMIN` no pueden acción científica.

| Operación | Acción/capacidad B2B.1 |
|---|---|
| listado, detalle, evidencia, metadatos de acciones | `view_foundations` / `RESEARCH_MANAGER` o `SYSTEM_ADMIN` |
| auditoría | `view_audit` / `RESEARCH_MANAGER` o `SYSTEM_ADMIN` |
| previsualización local | ninguna llamada de escritura; UI requiere capacidad de aplicar para habilitar confirmar |
| aplicar validar/corregir/vincular/descartar | `apply_scientific` / solo `RESEARCH_MANAGER` |
| revertir | `revert_scientific` / solo `RESEARCH_MANAGER` |

La UI recibe capacidades efectivas en `GET /human-review/me`, oculta acciones imposibles y muestra lectura restringida cuando proceda. Esto mejora UX, pero el endpoint repite la comprobación.

## 7. API propuesta

Rutas finales: `/api/v1/human-review/...`, coherentes con `api_router`. Respuestas incluyen `correlation_id`; comandos aceptan `Idempotency-Key` y también `correlation_id` UUID en el cuerpo. B2B.1 no tiene una tabla de claves idempotentes: antes de implementar hay un gate técnico. Sin cambio de esquema, se usará `correlation_id` dentro del payload/auditoría para detectar la misma operación por agregado en la transacción, pero no garantiza deduplicación global ante reintento tras timeout. Si se requiere idempotencia HTTP fuerte, necesitaría una tabla/migración; B2B.2 recomienda explícitamente no prometerla sin ese gate.

| Método/ruta | Capacidad | Contrato y resultado |
|---|---|---|
| `GET /human-review/cases` | view foundations | Query implementable: `page=1`, `page_size=25` (1–100), `status[]`, `case_type[]`, `period_id`, `document_key`, `source_revision`, `created_from`, `created_to`, `q`, `sort`. `q` busca solo `document_key` y `stable_target_key` (mínimo 3). `possible_duplicate=true` se normaliza a `case_type=possible_duplicate`; no es un segundo filtro. Respuesta `{items,total,page,page_size,facets,correlation_id}`. |
| `GET /human-review/cases/{id}` | view foundations | UUID. Devuelve `ReviewCaseDetail`: item/version, detected/normalized/canonical, decisión actual, decisión aplicada, overrides, memberships, links y resumen de evidencia. |
| `GET /human-review/cases/{id}/audit` | view audit | `page/page_size`, orden ascendente de cadena. Devuelve eventos sanitizados y decisiones relacionadas. |
| `GET /human-review/cases/{id}/evidence` | view foundations | Devuelve referencias/líneas/texto ya existente y, si existe archivo autorizado, URL firmada de corta vida o endpoint streaming autenticado; nunca `source_path`/bucket key. 503 si el objeto esperado no está disponible. |
| `POST /human-review/cases/{id}/apply` | apply scientific | `ApplyDecisionRequest`: `expected_version`, `expected_current_decision_id|null`, `action`, `scope`, payload discriminado tipado, `reason|null`, `correlation_id`. El cliente no envía `decision_type`: el servicio lo deriva de la tabla cerrada case/action/payload de §5 y rechaza el payload incompatible. Devuelve 200 con caso y decisión actualizados, resumen de proyección y `kpi_effect`. |
| `POST /human-review/cases/{id}/discard` | apply scientific | `DiscardRequest`: CAS, `reason` obligatorio, `correlation_id`; decisión `discarded`, estado científico `discarded`. |
| `POST /human-review/cases/{id}/revert` | revert scientific | `RevertRequest`: `expected_version`, `expected_current_decision_id`, `decision_id_to_revert`, `reason`, `correlation_id`; usa `FunctionalReversalCommandV1`. |
| `GET /human-review/me` | autenticado | Capacidades efectivas y acciones permitidas; no sustituye controles de cada endpoint. |

No se expone `POST /proposals`. La alternativa A (dos endpoints persistentes `proposals`/`apply`) queda descartada porque contradice la decisión funcional. La alternativa B (un endpoint con `mode=preview|apply`) queda descartada: preview es local y no debe convertirse en contrato de escritura. La alternativa C recomendada es un comando final por acción, con `/apply`, `/discard` y `/revert`; `action` discriminado evita endpoints por cada case type.

Errores tienen forma `{code,message,correlation_id,details?}`. 400 `HUMAN_REVIEW_VALIDATION`; 401 `AUTHENTICATION_REQUIRED`; 403 `B2B_CAPABILITY_REQUIRED`; 404 `REVIEW_CASE_NOT_FOUND`; 409 `REVIEW_CASE_VERSION_CONFLICT` (incluye versión/decisión actuales sanitizadas) o `INCOMPATIBLE_DECISION`; 422 `INVALID_COMMAND_PAYLOAD`; 500 `HUMAN_REVIEW_INTERNAL_ERROR`; 503 `EVIDENCE_UNAVAILABLE`. Nunca contienen SQL, rutas, secretos ni stack trace. En 409 la UI mantiene el formulario en memoria, muestra “este caso cambió”, ofrece recargar y no reintenta automáticamente.

## 8. Concurrencia, proyección y KPI

Cada comando identifica `review_item_id`, `expected_version`, `expected_current_decision_id`, actor autenticado, `correlation_id`, payload Pydantic cerrado y motivo cuando aplica. `HumanReviewCommandService` carga el caso, autoriza, valida tipo/acción, crea decisión aprobada y el snapshot completo; llama `set_current_decision`, que hace CAS sobre `review_items.version`; materializa overrides e identidades/aliases y añade auditoría con `append_audit_event_at_current_head`. Cualquier excepción revierte toda la sesión. La primera transacción incrementa versión; otra con la versión anterior recibe 409 y no agrega decisión, override ni evento.

Antes de agregar el evento de auditoría y dentro de la misma transacción, el comando calcula el `kpi_effect` a partir de la proyección previa y la propuesta; ese resumen tipado entra tanto en el payload de auditoría como en la respuesta. Tras commit, el mismo request serializa el caso desde la proyección y usa adaptadores de `ValidatedReadService` para preferir override humano bloqueado sobre columnas reconstruibles. `KpiService.dashboard()` debe consumir esos lectores adaptados; por eso la siguiente lectura HTTP refleja el cambio sin cola ni cache de servidor. El `kpi_effect` es advertencia de UX, no un segundo escritor. Reprocesamiento futuro debe consultar la proyección bloqueada antes de valores parser, preservando decisión humana.

La transacción, CAS, proyección y filtros básicos no requieren nueva tabla. Sin embargo, la auditoría exacta revela una insuficiencia real: `AuditEventType` solo acepta `case_backfilled`, eventos de identidad/override/capacidad, `audit_corrected` y `functional_reversion`. No existe un tipo válido para validar, corregir, vincular o descartar sin override; reutilizar `case_backfilled` u `override_created` falsearía el ledger.

Por tanto, **se requiere un gate humano de migración 0021 antes de implementar comandos B2B.2**. La migración propuesta (no creada en este diseño) amplía aditivamente el check de `audit_events` con `scientific_decision_applied`; el cambio de producto asociado extiende `AuditEventType`, `AuditPayloadV1`, `_PAYLOAD_KIND_BY_EVENT_TYPE` y el mapping de schemas en `human_review_audit.py`. El payload cerrado contiene `decision_id`, `decision_type`, estado previo/final, impacto KPI resumido y `review_item_id`. La reversión conserva `functional_reversion`. Alternativas sin migración: (a) solo `review_decisions`, insuficiente frente al requisito de evento; (b) reutilizar un tipo existente, semánticamente falso; (c) omitir auditoría, fuera de alcance. Ninguna es aceptable. Idempotencia HTTP fuerte es un segundo gate opcional: requeriría una tabla de deduplicación; B2B.2 puede limitarse a CAS y correlación sin prometer esa garantía.

## 9. Listado, evidencia y rendimiento

Orden predeterminado: `manual_priority DESC NULLS LAST`, `automatic_priority DESC`, `created_at ASC`, `id ASC`. Page/limit es consistente con el frontend y evita introducir cursor sin índice adecuado. Los índices actuales respaldan cola, documento, período y target. `case_type/status/prioridad` usan `ix_review_items_queue`; documento y período usan índices dedicados.

Matriz cerrada de filtros: `status`, `case_type`, `period_id`, `document_key`, `source_revision` (la fuente/revisión B2B.1), fecha de creación y texto libre de documento/stable key están respaldados directamente por `review_items`; se exponen. `possible_duplicate` es solo el alias UI de `case_type=possible_duplicate`; se expone sin join. `career_id` requiere un join polimórfico y se pospone de la API inicial: las rutas fuente son distintas y `external_researchers` no tiene carrera; se habilita únicamente tras especificar y probar las cinco ramas y semántica de nulos. `confidence_min/max` no existe en `review_items` ni tiene una representación común en las cinco fuentes; no se expone y requiere un contrato de consulta por fuente, no una migración de B2B.1 por conveniencia visual. `has_evidence` tampoco tiene columna de disponibilidad: página/sección no prueban que el PDF esté disponible; no se expone hasta que un query de evidencia seguro defina el criterio. El filtro de documento es `document_key`, no una búsqueda de contenido PDF. Máximo 100; los filtros no soportados se muestran como no disponibles, no se simulan.

El detalle distingue explícitamente detectado, normalizado, canónico/proyectado, propuesta de vista previa, decisión aplicada, overrides activos, documento, sección, locator, página y confianza. La evidencia se obtiene de `ImportedOcrTrace`, `ImportNormalizationAudit`, entidad fuente y almacenamiento ya existente. La URL PDF será firmada de corta vida por backend o streaming autenticado con control de capacidad; la respuesta nunca revela `source_path`, ruta MinIO o credenciales. Los fragmentos se limitan y sanitizan.

## 10. Frontend y experiencia

Rutas: `/human-review` para la bandeja y `/human-review/cases/[id]` para detalle. La primera reusa `AppShell`, `DataTable`, Tailwind, `api.ts`, `types.ts`, `useCachedQuery` y el filtro global de período, sin rediseño global. El filtro global de carrera se oculta/desactiva en la bandeja hasta que se autorice e implemente la matriz polimórfica de §9; no se envía `career_id` a la API inicial.

Componentes propuestos: `ReviewQueuePage`, `ReviewQueueFilters`, `ReviewQueueTable`, `ReviewPagination`, `ReviewCasePage`, `DetectedDataPanel`, `EvidencePanel`, `DecisionComposer`, `DecisionPreview`, `IdentityLinker`, `DiscardConfirmDialog`, `RevertConfirmDialog`, `AuditTimeline`, `OptimisticConflictDialog` y `CapabilityGate`. `human-review-api.ts` centraliza requests/errores, `human-review.ts` tipos discriminados y `useReviewCase`/`useReviewQueue` encapsulan cache/refetch.

El formulario produce un `DecisionDraft` local. La vista previa muestra estado nuevo, campos/links/overrides, cambio posible de KPI y motivo. Confirmar habilita solo con payload válido/capacidad; descarte y reversión exigen confirmación y motivo. Tras 200, invalida/refresca caso, cola, dashboard, producción/proyectos/docentes afectados y muestra éxito comprensible. Loading usa skeleton/mensaje; vacío explica filtros; 401 redirige a inicio de sesión; 403 muestra acceso no disponible; 404 vuelve a la bandeja; 409 abre diálogo de recarga; 503 permite reintentar evidencia sin afectar la decisión.

## 11. Seguridad, observabilidad y pruebas

Todos los comandos usan JWT, capability server-side, payloads `extra=forbid`, UUID/correlation ID, CAS, transacción PostgreSQL y auditoría encadenada. Se limita texto de búsqueda/fragmentos y se registra solo metadato seguro. Observabilidad: log estructurado por `correlation_id`, `review_item_id`, acción, resultado, latencia y `kpi_effect`; métricas de cola, 409, 403, 5xx, evidencia 503 y duración de comandos. No registrar payload de evidencia completo ni tokens.

TDD backend: schemas/errores, autorización, list/filtros/página, detalle/evidencia, matriz por caso, comandos, motivos, append-only, CAS en carreras paralelas, rollback, reversión, auditoría/cadena, proyección, lectores y KPI. Usar unit tests para mapeos/validadores, PostgreSQL 16 descartable para locks, constraints, JSONB, índices, transacciones y permisos; nunca SQLite para esos casos. Verificar que `ValidatedReadService` y `KpiService` cambian tras commit y que un reproceso no prevalece sobre lock humano.

Frontend: tests Node/componentes para render, filtros, paginación, capacidad/acciones ocultas, preview sin request, confirmación, loading/vacío/error, 409 y refetch. E2E contra API/PostgreSQL descartables para bandeja→detalle→evidencia→aplicar→vistas/KPI y revertir. La suite debe conservar regresiones B2B.1 y pruebas API existentes.

## 12. Riesgos, aceptación y tareas

Riesgos: (1) lectores actuales ignoran proyección; se mitiga con adaptación antes de exponer acciones; (2) joins polimórficos afectan filtros; se limita matriz soportada y se mide EXPLAIN; (3) entrega de PDF puede exponer almacenamiento; se encapsula en URL firmada/stream; (4) reintentos no tienen idempotencia fuerte; se declara el límite, no se oculta; (5) la transición directa `pending→resolved` amplía una regla B2B.1 y exige regresión de estados/CAS; (6) la taxonomía actual no cubre comandos científicos, por lo que 0021 es un gate obligatorio.

Criterios de aceptación: cada acción autorizada debe indicar endpoint, comando, tabla/evento/proyección/KPI; toda decisión aprobada actualiza lectores/KPI tras commit; ningún 409 sobrescribe datos; PDF no expone rutas internas; UI distingue detectado/corregido/evidencia; las exclusiones B2B.3–B2B.6 no aparecen; 0021 solo se crea tras el gate humano para la taxonomía de auditoría.

División propuesta: (1) gate y migración aditiva de taxonomía de auditoría; (2) schemas HTTP, dependencia de capacidades y consultas de cola/detalle/auditoría; (3) servicio de comandos, CAS, decisiones/proyección/auditoría; (4) adaptadores `ValidatedReadService` y pruebas KPI; (5) entrega segura de evidencia; (6) rutas/API y pruebas PostgreSQL de integración; (7) tipos/API client/hooks frontend; (8) bandeja/filtros/detalle/evidencia; (9) formularios, preview/conflicto/auditoría; (10) E2E, rendimiento, seguridad y regresión. Cada tarea empieza en rojo y no crea 0021 hasta el gate humano.

## 13. Gate humano

Este documento es solo diseño. No autoriza código, endpoint, UI, migración ni plan de implementación. Se requiere aprobación expresa de este diseño antes de crear el plan B2B.2.
