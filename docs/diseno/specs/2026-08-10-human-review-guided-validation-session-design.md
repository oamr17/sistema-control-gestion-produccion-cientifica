# Diseño — Sesión guiada de validación de registros

**Fecha:** 2026-08-10  
**Estado:** aprobado funcionalmente  
**Base:** B2B.2 COMPLETE; extensión compatible, sin iniciar B2B.3

## 1. Propósito

La pantalla de detalle de revisión humana debe permitir que un gestor sin conocimiento técnico valide una entidad científica completa, consulte la evidencia mientras edita y resuelva en el mismo momento varias revisiones pendientes relacionadas con esa entidad.

La experiencia se denomina **Sesión de validación**, pero no crea una sesión persistida. Los borradores y previews permanecen exclusivamente en memoria del navegador. Cerrar o recargar descarta todo lo no confirmado.

## 2. Decisiones aprobadas

- Se agrupan las revisiones pendientes de la misma entidad científica, aunque procedan de documentos diferentes.
- La asociación la resuelve el backend con datos persistidos existentes; el frontend no reconstruye identidades.
- El visor cambia automáticamente al documento, página y sección de la revisión activa.
- El gestor puede preparar varias decisiones antes de confirmar.
- `Confirmar todas` aplica cada revisión mediante su comando B2B.2 independiente.
- Se permiten resultados parciales: unas revisiones pueden confirmarse y otras permanecer pendientes.
- Las confirmadas siguen visibles, read-only y colapsadas.
- Las pendientes, inválidas o conflictivas conservan su borrador local.
- No se introduce un comando batch, una transacción global, autosave ni borradores persistentes.

## 3. Experiencia de usuario

### 3.1 Escritorio

La superficie principal usa una vista dividida:

- **Panel de evidencia:** documento de la revisión activa, nombre público, página, sección, fragmento seguro y alternativa para abrir o descargar.
- **Panel de validación:** encabezado de la entidad, progreso de la sesión, lista de revisiones y editor guiado de la revisión activa.

La lista de revisiones muestra estado y propósito en lenguaje operativo. Una revisión pendiente se expande al seleccionarla. Una revisión confirmada muestra un resumen y permanece colapsada.

### 3.2 Móvil

La evidencia aparece en un panel plegable encima del editor. El cambio de revisión actualiza el panel sin desplazar al usuario fuera del flujo. No se usa una tabla horizontal ni un diseño que requiera zoom.

### 3.3 Lenguaje guiado

El editor organiza la información en cuatro preguntas:

1. **Qué detectó el sistema.** Valor automático y contexto público.
2. **Qué debes verificar.** Explicación breve según `case_type`.
3. **Qué dato quedará registrado.** Campos editables con ejemplos y ayudas.
4. **Por qué se realiza el cambio.** Motivo y vista previa del resultado.

Los identificadores públicos avanzados permanecen disponibles cuando el contrato los exige, acompañados de explicación. No se muestran nombres de tablas, rutas, claves de almacenamiento ni identificadores internos.

## 4. Arquitectura

### 4.1 Consulta relacionada

Se añade una operación read-only:

`GET /api/v1/human-review/cases/{case_id}/related`

El servicio parte del caso autorizado y deriva internamente la entidad científica efectiva. Puede usar claves internas existentes para consultar, pero nunca las serializa. No agrupa por similitud textual del nombre.

La respuesta propuesta es tipada y sanitizada:

```text
RelatedReviewResponse
  entity:
    public_type
    public_id
    display_name
  items[]:
    case_id
    case_type
    case_status
    scientific_status
    version
    current_decision_id
    detected_value
    normalized_value
    canonical_value
    possible_kpi_impact
    allowed_actions[]
    evidence_summary
  total_pending
  correlation_id
```

Solo incluye casos pendientes que el actor puede consultar. La capacidad se valida sobre el caso inicial y sobre cada elemento relacionado. Si el modelo actual no puede determinar la misma entidad de forma inequívoca con datos existentes, la implementación debe detenerse; no se crea una migración por conveniencia visual.

### 4.2 Comandos

Se conservan sin ampliación:

- `POST /human-review/cases/{id}/apply`
- `POST /human-review/cases/{id}/discard`
- `POST /human-review/cases/{id}/revert`

Cada comando mantiene su propio `expected_version`, `expected_current_decision_id`, `correlation_id`, autorización, transacción, decisión append-only, proyección y auditoría.

`Confirmar todas` es coordinación de interfaz. Procesa solamente borradores válidos con concurrencia máxima de dos comandos. No reintenta automáticamente y no promete idempotencia HTTP fuerte.

### 4.3 Evidencia

Se reutiliza `GET /human-review/cases/{id}/evidence`. Solo se carga la evidencia de la revisión activa. Al cambiar de revisión se cancela una carga obsoleta y se revoca el blob anterior.

Para PDF, el visor intenta abrir el blob local en la página indicada y muestra siempre página, sección y fragmento como respaldo. Si el navegador no puede incrustarlo, ofrece apertura o descarga autenticada. La interfaz no contiene Dropbox paths, buckets, object keys, credenciales ni URLs permanentes de infraestructura.

## 5. Estado frontend

El estado se normaliza por `case_id`:

```text
draftByCaseId
resultByCaseId
validationByCaseId
conflictByCaseId
requestStateByCaseId
activeCaseId
activeEvidence
```

Cada borrador contiene acción, scope, payload, motivo y correlation ID. Cambiar de revisión, abrir/cerrar preview, cambiar foco o actualizar evidencia no reinicializa el borrador.

Un borrador se elimina únicamente por:

- cancelación explícita de esa revisión;
- limpieza total confirmada;
- confirmación satisfactoria de esa revisión;
- cierre o recarga de la página.

Una recarga explícita por 409 reemplaza solamente CAS y baseline del caso afectado conforme al contrato vigente, conservando el borrador compatible para revisión manual.

## 6. Componentes

- `ReviewSessionPage`: composición y carga del contexto relacionado.
- `EvidenceWorkspace`: visor, locator público, fallback y ciclo de vida del blob.
- `RelatedReviewList`: selección, progreso y estados colapsados.
- `GuidedDecisionEditor`: campos y ayudas por tipo de caso.
- `SessionConfirmationBar`: validación y confirmación parcial.
- `ReviewResultSummary`: resultado seguro por revisión.

Los componentes reciben datos tipados y callbacks específicos. El estado de comandos no se mezcla con el estado del documento. Se evitan componentes definidos dentro del render y efectos dependientes de objetos recreados.

## 7. Flujo de confirmación parcial

1. Se validan localmente todos los borradores.
2. Los inválidos permanecen abiertos y no generan request.
3. Los válidos se colocan en cola con concurrencia máxima de dos.
4. Cada respuesta actualiza únicamente su `case_id`.
5. Un 200 colapsa la revisión como completada y refresca su proyección.
6. Un 409 conserva el borrador, bloquea una nueva confirmación de ese caso y ofrece recarga explícita.
7. Un 403, 404, 422 o 503 deja la revisión abierta con mensaje sanitizado y correlation ID cuando exista.
8. El resumen final indica confirmadas, pendientes y conflictivas.

No se revierte una confirmación exitosa porque otra revisión falle.

## 8. Accesibilidad y responsive

- Navegación completa por teclado y foco visible.
- Encabezados, regiones y estados con semántica adecuada.
- Progreso y resultados anunciados mediante regiones `aria-live` sin repetir mensajes.
- Cada revisión identifica su estado sin depender solo del color.
- El visor tiene título accesible y fallback textual.
- En móvil no hay overflow horizontal, controles fuera de pantalla ni scroll atrapado.
- Se respeta `prefers-reduced-motion`.

## 9. Rendimiento React/Next.js

- Consulta relacionada y capacidades se inician sin waterfalls evitables.
- Evidencia se carga bajo demanda, solo para la revisión activa.
- Las listas usan `case_id` estable y estado normalizado.
- Los componentes de revisión se aíslan para evitar rerenderizar todos los formularios al escribir en uno.
- Las actualizaciones de borrador usan estado funcional cuando dependen del valor anterior.
- No se persisten datos científicos en `localStorage`, `sessionStorage` ni IndexedDB.

## 10. Seguridad

- Autenticación y capacidad se validan server-side en consulta y comandos.
- La asociación por entidad nunca amplía el alcance autorizado del actor.
- Schemas cerrados con campos públicos explícitos.
- No se serializan `stable_target_key`, `document_key` crudo, rutas, nombres de bucket, claves ni credenciales.
- La evidencia sigue siendo read-only y autenticada.
- Los errores no incluyen SQL, stack traces ni detalles de infraestructura.
- No se registran payloads completos de evidencia ni contenido sensible en logs.

## 11. Pruebas

### Backend

- agrupación inequívoca por entidad existente;
- exclusión de coincidencias solo nominales;
- autorización individual y prevención de acceso cruzado;
- respuesta sanitizada sin claves internas;
- consulta read-only y sin eventos de auditoría;
- paginación o límite seguro si la entidad tiene muchos casos;
- PostgreSQL 16 descartable y validación de índices existentes;
- ausencia de migraciones.

### Frontend

- varios borradores sobreviven al cambio de revisión;
- documento, página y sección siguen la revisión activa;
- blob anterior se revoca y una respuesta obsoleta no reemplaza la evidencia activa;
- preview local no escribe;
- combinación de 200, 409, 403, 422 y 503 produce resultados independientes;
- no hay doble envío ni reintento automático;
- confirmadas visibles y colapsadas;
- cancelación individual y limpieza total confirmada;
- cierre/refresh descarta borradores;
- accesibilidad, desktop, móvil, consola e hidratación;
- TypeScript, build y regresiones completas B2B.2.

## 12. Criterios de aceptación

- Un gestor puede ver todas las revisiones pendientes autorizadas de la misma entidad científica.
- Puede editar varias sin perder cambios al navegar entre ellas.
- Puede verificar cada dato contra su evidencia sin abandonar la sesión.
- `Confirmar todas` genera un resultado independiente y auditable por caso.
- Los éxitos se mantienen visibles y colapsados; los fallos conservan borrador.
- Ningún preview, cambio de selección o consulta de evidencia escribe datos.
- No existen borradores persistidos, autosave, comando batch ni transacción global.
- No se exponen rutas o identificadores internos.
- No se modifica esquema ni se crea migración.
- B2B.3 permanece fuera de alcance.

## 13. Fuera de alcance

- colaboración simultánea;
- ownership, reservas o expiración;
- recuperación de borradores;
- carga, edición o regeneración de evidencia;
- OCR, parser, Dropbox o n8n;
- idempotencia HTTP fuerte;
- reversión automática de éxitos parciales;
- cambios de esquema o migraciones;
- funciones B2B.3–B2B.6.

