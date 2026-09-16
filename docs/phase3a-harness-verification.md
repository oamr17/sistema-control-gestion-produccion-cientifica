# Phase 3A — modernización del harness histórico

Fecha de cierre de verificaciones: 2026-09-05.

Autorización: `e473c56e-1f57-445e-935b-8c3534c8149b/pasted-text.txt`,
POST-DELIVERY CLEANUP — PHASE 3A RESUME AUTHORIZED.

## Alcance y decisiones

La reanudación modifica pruebas, factories, preparación de catálogos de prueba,
documentación de pruebas y el comando `test` de `frontend/package.json`.
Los cambios de producto que ya estaban en el worktree pertenecen a fases anteriores.
El cambio de Next 14.2.23 a 14.2.35 visible en el diff contra HEAD es anterior a
esta reanudación; esta fase sólo añade `--test-concurrency=1` al script `test`.

- T01 UPDATED: los casos resueltos usan facultad/carrera realmente sembradas;
  los casos sin ámbito conservan una razón no resuelta válida. Los mocks con
  ámbito resuelto usan `scope_resolution_reason=None`, como exige 0022.
- T02 UPDATED: catálogos históricos desechables completos para el preflight
  0022 y usuarios de prueba con las columnas actuales. Se reutiliza el helper
  de catálogo que ya existía en el trabajo pausado. Las migraciones no cambian.
- T03 UPDATED: referencias de identidad opcionales y generadas por servidor;
  transición `reopened -> resolved` vigente; autorización por rol y ámbito
  persistido; datos demo deterministas actuales; planes de consulta que también
  admiten los índices únicos y de ámbito actuales.
- T04 ALREADY COMPLETE: se conserva la cobertura E2E de Human Review y C-01.
  Se corrige únicamente la ejecución concurrente de archivos del harness.
- S14 / identidad ALREADY COMPLETE: el guard compartido ya inspecciona el AST
  y exige que el export por defecto renderice `LegacyProjectsPage`. La prueba
  de identidad utiliza los delimitadores actuales y el lector `/projects`.
  No se reintrodujo la isla eliminada.

## Evidencia de las correcciones

`b2a-contracts.test.mjs` fallaba 4/4 por cardinalidades históricas 49/19/2 y
por exigir `director_validation_status` en la pantalla de Proyectos.
El seed académico actual contiene Ana Torres y Carlos Vera, cinco productos
deterministas y cero productos elegibles para KPI. Se verificaron los endpoints
y el seed antes de cambiar las expectativas. La prueba protege esos registros,
las autorías y el KPI fail-closed; no usa aserciones vacías de cardinalidad.
Proyectos consume `/projects` y `project.teachers`, incluida la identidad efectiva;
no depende de presentar `director_validation_status` del lector histórico.

El módulo related reprodujo 21 errores FK porque los casos referían la carrera 1
pero el setup sólo creaba la facultad 1. La carrera se añadió al setup. Sus tres
tests HTTP usaban un rol histórico inexistente; ahora usan el rol actual con
facultad. Las aserciones 404 indistinguible y 503 sanitizado permanecen.

Privilegios reprodujo dos violaciones del CHECK de ámbito; el caso SQL de prueba
ahora declara `unresolved_no_persisted_scope`. Proyección reproducía tres rechazos
esperados basados sólo en capability. Ahora prueba actores sin ámbito, inactivos
o inexistentes y mantiene la comprobación de ausencia de escrituras.

El antiguo barrier colocado en CAS bloqueaba dos comandos que actualmente se
serializan antes mediante locks. Se desplazó el rendezvous a la entrada de los
dos clientes/sesiones, antes de los locks. Permanecen las aserciones de un ganador,
un conflicto, un evento y un incremento de versión. No se modificaron locks ni CAS.

La revisión independiente de solo lectura detectó dos ajustes: conservar ese
rendezvous antes de los locks y usar razón NULL en los mocks resueltos. Ambos se
aplicaron y la segunda revisión no dejó hallazgos Important ni Critical.

## Timeout concurrente frontend

Clasificación: **A — aislamiento del harness, resuelto sólo en configuración de pruebas**.

1. Caso 401/404 aislado: 1/1 PASS.
2. Human Review serializado: 16/16 PASS.
3. Suite global con archivos concurrentes: reaparecieron timeouts en Human Review.
   La salida de esa ejecución quedó interrumpida; no se usa como conteo final.
4. Reproducción reducida con los dos archivos de navegador concurrentes:
   Human Review 16 PASS, login 1 cancelado por timeout de 60 segundos.
   Se verificaron dos puertos distintos, 63725 y 63726. Ambos procesos Next
   compartían `.next` y usaban configuraciones de API distintas.
5. `npm test` ahora serializa archivos mediante `--test-concurrency=1`.
   Las pruebas de dos clientes y coordinadores concurrentes dentro de cada
   archivo siguen ejecutándose y mantienen sus aserciones.

No se aumentaron timeouts, eliminaron tests ni cambiaron respuestas de producto.

## Entorno y reproducción

Windows, Node.js local, PostgreSQL 16 en el proyecto Docker desechable
`prototype-phase3a`. Se usa `.env.example`, con cuentas y credenciales explícitamente
académicas, y la base desechable `b2b2_task3` con
`application_name=b2b1_disposable_migration_test`.
Los contenedores de prueba montan la raíz del repositorio en `/workspace`,
necesaria para los contratos backend que inspeccionan archivos frontend.

La selección focal backend incluye audit, authorization, commands, concurrency,
E2E, KPI, readers, reversal, privileges, projection, scope_migration_0022,
backfill y canonical_identity, canonical_identity_backfill y
canonical_identity_migration. La suite completa usa el discovery legítimo:

```powershell
docker compose --env-file .env.example -p prototype-phase3a run --rm --no-deps -v "${PWD}:/workspace" -e "B2B1_TEST_DATABASE_URL=postgresql+psycopg://prototype_owner:local-owner-password@postgres:5432/b2b2_task3?application_name=b2b1_disposable_migration_test" backend python -B -m unittest discover -s tests -q
```

Para el conteo final se ejecutó exactamente ese discovery con un `TextTestResult`
que también cuenta `addSuccess` y exporta cada error y skip; no altera la suite,
los resultados ni el exit code. Los tracebacks controlados de tests negativos
quedan en la salida del servidor y no se cuentan como fallos de unittest.

Resultados finales:

- Backend focal: 256 ejecutadas, 256 PASS, 0 fallos, 0 errores; 2 skips de
  `setUpClass` por ausencia de `CANONICAL_IDENTITY_TEST_DATABASE_URL`.
- Backend amplio: 824 ejecutadas, 775 PASS, 0 fallos, 46 errores de subtest por
  fixtures ausentes y 8 skips registrados. Dos skips son de `setUpClass`; los
  otros seis corresponden a `DROPBOX_VERSIONING_TEST_DATABASE_URL` y
  `TASK7_POSTGRES_URL` no configurados.
- Frontend focal: 126/126 PASS.
- C-01 focal: 2/2 PASS; Human Review E2E: 16/16 PASS.
- Frontend amplio: 143/143 PASS, 0 fallos, 0 skips y 0 cancelaciones.
- TypeScript y build de producción: PASS.
- `npm audit --omit=dev`: Critical 0, High 3. La resolución ofrecida instala
  Next 16.3.4 y Sharp 0.35.4 mediante `--force`; permanece diferida por ser major.
- Integridad: hashes 0021 y 0022 exactos, 0023 ausente, guards S13/S14/S15
  10/10 PASS, `git diff --check` PASS, reportes y PDF demo versionables.

Los 46 errores amplios quedan clasificados como
`MISSING HISTORICAL/EXTERNAL FIXTURE`. Los nombres `job234_ginfaes.pdf` y
`job242_tributaria.pdf` son resueltos por el test geométrico desde el directorio
histórico `backend/reports/identity_product_investigation_20260711/`; tampoco
existen allí. No queda ningún error de producto, harness obsoleto o causa
desconocida.

Desde `frontend`, los comandos de verificación son:

```powershell
node --test --test-concurrency=1 --test-name-pattern="401 and 404 detail responses stay distinct" tests/human-review-e2e.test.mjs
node --test --test-concurrency=1 tests/human-review-e2e.test.mjs
npm test
npx tsc --noEmit --incremental false
npm run build
```

La selección focal frontend ejecuta todos los archivos `*.test.mjs` excepto
`human-review-e2e.test.mjs` y `login-page.test.mjs`; éstos tienen resultados
separados y también forman parte del `npm test` completo. TypeScript y build
se ejecutaron secuencialmente.

## Fixtures y artefacto QA

Se confirmó la ausencia de estos cuatro archivos; no se crearon ni sustituyeron:

- `backend/data/reference/base_investigadores_fca.xlsx`
- `backend/tests/fixtures/zambrano_fci021_2025.pdf`
- `backend/tests/fixtures/job234_ginfaes.pdf`
- `backend/tests/fixtures/job242_tributaria.pdf`

`c01-real-login-qa.mj`: **OTHER / NOT FOUND**, action taken **NONE**.
El archivo histórico con extensión `.mjs` estaba fuera del repo en
un directorio temporal de verificación (`c01-real-login-qa.mjs`);
ya no existe. Era un artefacto QA temporal retirado antes de esta autorización.
Esta reanudación no modificó ni eliminó ninguna de las dos variantes.

El stack desechable `prototype-phase3a` fue retirado al terminar mediante
`docker compose ... down -v --remove-orphans`; no se ejecutó `docker prune`.
