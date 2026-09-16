# Guía de entrega del prototipo académico

## 1. Propósito y alcance

Este documento permite a otros tesistas instalar, ejecutar, probar y continuar el
Sistema de Control de Producción Científica desde una clonación limpia. Describe el
estado actual del repositorio público, no el entorno privado usado durante la tesis.

El entregable es un **prototipo académico**, no un despliegue listo para producción.
Se conservan usuarios y datos demo porque forman parte del recorrido verificable. La
experiencia móvil está fuera del alcance y no fue rediseñada.

## 2. Arquitectura actual

| Área | Responsabilidad | Ubicación |
| --- | --- | --- |
| Frontend | Login, dashboard, participantes, producción, proyectos, POA, reportes y validación humana | `frontend/app`, `frontend/components`, `frontend/lib` |
| Backend | API FastAPI, reglas de negocio, importación, identidad, evidencias y reportes | `backend/app` |
| Datos | PostgreSQL 16 con rol propietario de bootstrap y rol limitado de aplicación | `docker-compose.yml`, `backend/app/models` |
| Evidencias | Archivos PDF locales del prototipo en MinIO | `backend/app/api/v1/endpoints/evidence.py` |
| Bootstrap | Baseline verificado y seed demo para una base nueva | `backend/scripts/init_db.py` |
| Automatización opcional | Dropbox y n8n para enviar metadatos de documentos al backend | `docs/automatizacion-dropbox-n8n.md` |
| Pruebas | `unittest`, Node test runner, compilación y QA desktop real | `backend/tests`, `frontend/tests` |

n8n no es una dependencia de frontend, backend, PostgreSQL ni MinIO. El sistema
principal funciona con `N8N_WEBHOOK_URL` vacío; la respuesta de alertas externas queda
en `skipped`. Tampoco se distribuye un export de workflow: la guía n8n es una receta
manual opcional.

## 3. Entorno reproducible

### Requisitos

- Docker Desktop y Docker Compose.
- Git.
- PowerShell para seguir literalmente los comandos públicos.
- Python 3.12 y Node.js/npm para las suites ejecutadas en el host.

### Archivo de entorno

Desde la raíz:

```powershell
Copy-Item .env.example .env
```

`.env.example` contiene valores locales deterministas. No contiene credenciales de
infraestructura reales. `.env` está ignorado y no debe versionarse.

`DEMO_MODE` es explícito y opt-in: solo `true` habilita acciones demo. La ausencia de
la variable no las habilita. El entorno académico entregado usa:

```text
DEMO_MODE=true
SEED_DEMO_DATA=true
```

### Secretos que nunca deben entrar al repositorio

- Contraseñas o usuarios PostgreSQL reales.
- `JWT_SECRET` real.
- Credenciales o endpoints internos MinIO.
- Tokens o credenciales Dropbox.
- API keys reales.
- Connection strings reales.

Las cuentas demo de la aplicación se excluyen deliberadamente de esta clasificación:
son un requisito aceptado del prototipo.

## 4. Bootstrap y arranque

1. Iniciar la infraestructura obligatoria:

   ```powershell
   docker compose --project-name scientific-prototype-demo --env-file .env up -d --wait postgres minio
   ```

2. Aplicar el baseline y seed sobre la base nueva:

   ```powershell
   docker compose --project-name scientific-prototype-demo --env-file .env --profile prototype-bootstrap run --rm --build prototype-bootstrap
   ```

3. Iniciar solo la aplicación principal:

   ```powershell
   docker compose --project-name scientific-prototype-demo --env-file .env up -d --build backend frontend
   ```

4. Comprobar `http://localhost:8000/health` y abrir
   `http://localhost:3000`.

5. Detener conservando volúmenes:

   ```powershell
   docker compose --project-name scientific-prototype-demo --env-file .env down
   ```

6. En una verificación desechable, y solo después de comprobar el nombre exacto del
   proyecto, retirar también sus volúmenes:

   ```powershell
   docker compose --project-name scientific-prototype-demo --env-file .env down --volumes
   ```

No use comandos globales de prune. El bootstrap es puntual; el backend permanente no
crea tablas, no aplica migraciones y no ejecuta seeds al iniciar.

## 5. PROTOTYPE VERIFIED SCHEMA BASELINE

El bootstrap solo acepta una base académica vacía. En orden:

1. confirma que la base sea nueva;
2. crea el metadata SQLAlchemy actual;
3. instala y verifica el manifiesto cerrado de objetos runtime;
4. comprueba el fingerprint esperado;
5. verifica los hashes inmutables de `0001` a `0022`;
6. aplica y comprueba el ACL de mínimo privilegio;
7. registra atómicamente el baseline;
8. carga el seed demo si el modo demo y el seed están habilitados.

El rol `prototype_owner` se utiliza para bootstrap. El serving recibe únicamente la
URL de `prototype_app`. Este mecanismo no reconstruye la historia de migraciones sobre
una base existente y no debe presentarse como arquitectura de migración de producción.

Los hashes versionados son parte del contrato. No edite una migración aplicada; cree
una nueva migración y sométala a revisión independiente.

## 6. Credenciales demo y recorrido funcional

- Facultad: `admin@university.edu` / `Admin123*`.
- Carrera: `adm.manager@university.edu` / `Manager123*`.

Recorrido sugerido:

1. iniciar sesión con la cuenta de facultad;
2. seleccionar `2025-2026` y `Ciclo 2`;
3. revisar Dashboard, Participantes, Producción científica, Proyectos, Validación de
   Registros y Reportes;
4. confirmar que el caso A muestra una identidad coherente en los tres módulos;
5. confirmar que la cuenta de carrera solo ve el caso A y que la de facultad también
   ve el caso B;
6. comprobar corrección y reversión desde el historial del caso.

Los documentos demo asociados son sintéticos y no representan producción científica
real.

## 7. Identidad efectiva y semilla opcional

La presentación coherente de Participantes, Producción científica y Proyectos se basa
en las relaciones e identificadores persistidos y en las decisiones de validación
humana. No se añadió un fallback general por nombre.

`backend/data/reference/base_investigadores_fca.xlsx` es solo un enriquecimiento
opcional:

- si la ruta predeterminada no existe, el parser continúa con cero registros semilla;
- su ausencia no bloquea `parse_progress_report()`;
- sin semilla no se crean coincidencias ni mezclas nominales nuevas;
- las pruebas de resolución usan una semilla sintética determinista;
- una ruta proporcionada explícitamente conserva el comportamiento existente;
- una referencia explícita ausente, corrupta o inválida falla de forma visible.

No hay una variable pública que convierta ese Excel en requisito de runtime. Los
tesistas pueden inyectar una referencia explícita al probar el servicio, pero deben
controlar su procedencia y nunca versionar datos personales sin autorización.

## 8. Importaciones, Dropbox y n8n

El backend expone endpoints protegidos para importaciones. n8n puede listar PDF en
Dropbox y enviar al backend un JSON de metadatos; el backend realiza la descarga y el
procesamiento con sus propias credenciales.

La integración es opcional:

- `.env.example` deja vacías las credenciales Dropbox y `N8N_WEBHOOK_URL`;
- el servicio principal arranca y funciona sin n8n;
- la ausencia de credenciales solo falla al invocar una operación Dropbox;
- la ausencia del webhook no impide las alertas internas;
- no hay workflow exportado que importar.

Para experimentar con ella, inicie `n8n` explícitamente y cree el flujo según
`docs/automatizacion-dropbox-n8n.md`. No coloque tokens ni claves reales en capturas,
exports, documentación o historial Git.

## 9. Evidencias y respuestas públicas

El contrato de evidencias exige:

- MIME y extensión dentro de las listas configuradas;
- tamaño máximo configurable mediante `EVIDENCE_MAX_BYTES`;
- nombre de archivo sanitizado;
- ningún JWT en una URL;
- ninguna ruta interna de MinIO en respuestas públicas;
- ninguna URL entregada al cliente con `localhost` hardcoded fuera de la
  configuración local explícita.

Los tracebacks completos permanecen en logs del servidor. La API debe responder con
mensajes públicos sanitizados y nunca serializar una traza al frontend.

## 10. Contrato público de pruebas

### Backend

La suite normal es el discovery completo de `unittest`. Las integraciones B2B1/B2B2
sí forman parte del contrato y requieren una PostgreSQL 16 desechable llamada
`b2b2_task3`, con `application_name=b2b1_disposable_migration_test`.

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
docker run --name scientific-prototype-test-db --rm -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=b2b2_task3 -p 127.0.0.1::5432 -d postgres:16-alpine
$testPort = (docker port scientific-prototype-test-db 5432/tcp -split ":")[-1]
do { Start-Sleep -Seconds 1; $testReady = docker exec scientific-prototype-test-db psql -U postgres -d b2b2_task3 -Atc "SELECT current_database()" 2>$null } until ($testReady -eq "b2b2_task3")
cmd /d /s /c "set B2B1_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:$testPort/b2b2_task3?application_name=b2b1_disposable_migration_test&& .venv\Scripts\python -B -m unittest discover -s tests -v"
docker stop scientific-prototype-test-db
```

Resultado limpio verificado: `831 run / 820 pass / 11 skipped / 0 failures /
0 errors`. Las pruebas B2B1/B2B2 configuradas mediante la URL desechable sí se
ejecutaron. El inventario completo de skips es:

| Categoría | Test ID | Motivo explícito |
| --- | --- | --- |
| I | `test_canonical_identity_backfill.CanonicalIdentityBackfillPostgresIntegrationTests.setUpClass` | `CANONICAL_IDENTITY_TEST_DATABASE_URL is not set` |
| I | `test_canonical_identity_migration.CanonicalIdentityMigrationPostgresIntegrationTests.setUpClass` | `CANONICAL_IDENTITY_TEST_DATABASE_URL is not set` |
| I | `test_dropbox_versioning_integration.DropboxVersioningPostgresIntegrationTest.test_concurrent_promotions_keep_newest_current_for_either_lock_order` | `requiere DROPBOX_VERSIONING_TEST_DATABASE_URL` |
| I | `test_dropbox_versioning_integration.DropboxVersioningPostgresIntegrationTest.test_database_rejects_second_job_for_same_document_and_revision` | `requiere DROPBOX_VERSIONING_TEST_DATABASE_URL` |
| I | `test_dropbox_versioning_integration.DropboxVersioningPostgresIntegrationTest.test_failed_revision_does_not_displace_current_version` | `requiere DROPBOX_VERSIONING_TEST_DATABASE_URL` |
| I | `test_dropbox_versioning_integration.DropboxVersioningPostgresIntegrationTest.test_older_job_finishing_late_does_not_displace_newer_current_job` | `requiere DROPBOX_VERSIONING_TEST_DATABASE_URL` |
| I | `test_dropbox_versioning_integration.DropboxVersioningPostgresIntegrationTest.test_successful_new_revision_displaces_previous_only_on_promotion` | `requiere DROPBOX_VERSIONING_TEST_DATABASE_URL` |
| I | `test_prototype_verified_schema_baseline.DisposablePostgresCatalogProofTests.test_fresh_verified_baseline_matches_catalog_and_acl` | `TASK7_POSTGRES_URL is required for the disposable PostgreSQL 16 catalog proof` |
| H | `test_geometric_table_extractor.GeometricTableExtractorTest.test_page_without_production_header_returns_fallback` | `requires independently managed historical PDF fixture` |
| H | `test_geometric_table_extractor.GeometricTableExtractorTest.test_zambrano_pdf_preserves_author_column_numbers_and_provenance` | `requires independently managed historical PDF fixture` |
| H | `test_pdf_parser.PdfParserTest.test_zambrano_real_pdf_matches_verified_scientific_production_fixture` | `external historical PDF fixture is not part of the public prototype distribution` |

Categoría H significa artefacto histórico externo; I significa integración opt-in.
No hay skips de comportamiento de producto ni skips desconocidos.

Los archivos `job234_ginfaes.pdf`, `job242_tributaria.pdf`,
`zambrano_fci021_2025.pdf` y `base_investigadores_fca.xlsx` no son dependencias de la
suite pública. No deben copiarse desde un computador privado para ejecutar las pruebas
normales.

### Frontend

El contrato soportado evita los tres harnesses históricos que importan Playwright no
declarado. Con el backend local levantado:

```powershell
Set-Location frontend
npm ci
node --test --test-concurrency=1 tests/shared-api-surface.test.mjs tests/projects-effective-identity.test.mjs tests/human-review-validation-regressions.test.mjs tests/human-review-queue.test.mjs tests/human-review-decisions.test.mjs tests/human-review-conflict.test.mjs tests/human-review-api.test.mjs tests/b2a-contracts.test.mjs
npx tsc --noEmit --incremental false
npm run build
npm audit --omit=dev
```

`login-page.test.mjs`, `human-review-e2e.test.mjs` y
`human-review-detail.test.mjs` son harnesses históricos opcionales. No se contabilizan
como errores del contrato público y no justifican agregar una dependencia nueva en
esta fase. La cobertura desktop final se ejecuta con un navegador real.

El contrato frontend limpio terminó `102/102 PASS`, TypeScript pasó y el build generó
12 páginas. `npm audit --omit=dev` registró exactamente `0 Critical / 3 High`: Next.js,
PostCSS transitivo y Sharp. npm solo ofrece `--force` con cambios breaking
(`next@16` y `sharp@0.35`); no existe una remediación compatible con el lock actual.
El riesgo queda diferido a una migración mayor aprobada. No ejecute
`npm audit fix --force` sin ese diseño y autorización.

## 11. QA desktop mínima

Con la aplicación iniciada, verificar Login, Dashboard, Participantes, Producción
científica, Proyectos, Validación de Registros y Reportes. El criterio de aceptación
por recorrido es:

```text
broken images = 0
console errors = 0
hydration errors = 0
unexpected failed requests = 0
```

Los assets usados en Login deben revisarse expresamente. La revisión conserva el
layout, navegación y lenguaje visual desktop existentes. Los cambios de UX desktop
definidos para casos legibles de Validación están autorizados; móvil permanece fuera
de alcance.

## 12. Seguridad y límites conocidos

- El prototipo no incorpora un gestor centralizado de secretos.
- Los valores locales de `.env.example` no son adecuados para despliegues compartidos.
- La estrategia de baseline solo sirve para una base académica nueva.
- Dropbox y n8n requieren configuración externa si se quieren demostrar.
- La suite histórica basada en documentos reales queda fuera del contrato público.
- No se garantiza experiencia móvil ni preparación para producción.

Los imports grandes no deben refactorizarse por tamaño. No se requiere una
refactorización proactiva de `imports.py` o `import_service.py`; solo podrían extraerse
helpers operativos si una corrección confirmada toca esa responsabilidad y la
extracción fuese el cambio seguro mínimo para implementarla y probarla.

## 13. Higiene del repositorio

Una entrega pública no debe incluir:

- `.env` ni credenciales reales;
- `node_modules`, `.next`, caches Python o `*.tsbuildinfo`;
- backups, dumps, reportes locales o volúmenes;
- documentos personales de tesis;
- material histórico externo usado para investigación;
- rutas privadas como `C:\Users\...`;
- archivos de trabajo de herramientas locales.

Los informes históricos en `docs/` sirven como trazabilidad, pero no reemplazan esta
guía ni definen dependencias actuales.

## 14. Siguiente desarrollo recomendado

Antes de ampliar funcionalidad, cree una rama propia y repita el arranque desde una
clonación limpia. Después, priorice según la investigación siguiente: despliegue
seguro, migraciones incrementales para bases existentes, observabilidad, backups,
gestión real de secretos e integración n8n exportable. Mantenga intactos los usuarios
demo mientras el sistema continúe siendo un prototipo académico.
