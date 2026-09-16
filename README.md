# Sistema de Control de Producción Científica

Sistema prototipo para centralizar participantes, producción científica,
proyectos, metas POA, evidencias e importaciones de la Facultad de Ciencias
Administrativas. Usa FastAPI, Next.js, PostgreSQL, MinIO y Docker Compose.

Este repositorio es un **prototipo**. Las cuentas demo forman parte del
entregable y permiten recorrer los flujos principales. Un uso institucional en
producción requiere trabajo adicional de seguridad, operación, observabilidad,
respaldo y gobierno de datos.

Se publica como portafolio y referencia técnica. No incluye una licencia de
software; todos los derechos quedan reservados. La publicación no autoriza la
redistribución ni la creación de obras derivadas fuera de los usos permitidos por
GitHub. La documentación técnica está en
[docs/prototype-handoff.md](docs/prototype-handoff.md).

## Requisitos

- Git.
- Docker Desktop con Docker Compose.
- Puertos locales `3000`, `8000`, `5432`, `9000` y `9001` disponibles.
- Puerto `5678` disponible si se utiliza n8n.
- Para pruebas locales: Node.js 22 con npm para frontend y Python 3.12 para backend,
  las versiones de referencia de los Dockerfiles. No son necesarios en el equipo
  anfitrión para iniciar la aplicación con Docker.

Los comandos de esta guía están escritos para PowerShell y usan los puertos de
`.env.example`.

## Instalación y configuración

Clone el repositorio:

```powershell
git clone https://github.com/oamr17/sistema-produccion-cientifica-portafolio.git
Set-Location sistema-produccion-cientifica-portafolio
```

Desde la raíz del clon, cree el archivo local de entorno una sola vez. Si ya existe
`.env`, conserve su configuración:

```powershell
Copy-Item .env.example .env
```

`.env.example` contiene valores de ejemplo exclusivamente locales. `DEMO_MODE` es
opt-in: solo el valor literal `true` habilita acciones demo. Para este entorno
de demostración se entrega `DEMO_MODE=true` y `SEED_DEMO_DATA=true`. La ausencia de
`DEMO_MODE` no habilita el modo demo.

No versione `.env` ni sustituya los valores de ejemplo por credenciales reales en
archivos versionados. Las credenciales reales de PostgreSQL, JWT, MinIO, Dropbox,
las claves de API y las cadenas de conexión deben permanecer fuera de Git,
incluso en un repositorio privado.

## Cómo iniciar el sistema

La inicialización está diseñada para una base nueva de demostración. Ejecute estos
comandos desde la raíz del repositorio. Primero levante PostgreSQL 16 y MinIO:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env up -d --wait postgres minio
```

Luego ejecute una sola vez la inicialización de la base de datos:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env --profile prototype-bootstrap run --rm --build prototype-bootstrap
```

El proceso comprueba que la base esté vacía, crea la estructura actual, verifica
su integridad y los permisos de la aplicación, y carga los datos demo cuando
`DEMO_MODE=true` y `SEED_DEMO_DATA=true`. Una segunda ejecución sobre la misma base
se rechaza deliberadamente. Para una base ya inicializada, continúe directamente
con el arranque de la aplicación.

Inicie la aplicación principal; n8n no es necesario para el uso normal:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env up -d --build backend frontend
```

Compruebe el backend:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Accesos locales:

- [Aplicación](http://localhost:3000)
- [API](http://localhost:8000)
- [Documentación interactiva de la API](http://localhost:8000/docs)
- [Consola MinIO](http://localhost:9001)

Los reinicios del backend conservan la base existente: no crean tablas ni vuelven
a cargar los datos demo.

## Usuarios demo

Estas cuentas son credenciales **exclusivamente demo** y forman parte del prototipo:

- Facultad: `admin@university.edu` / `Admin123*` (`FACULTY_ADMIN`).
- Carrera: `adm.manager@university.edu` / `Manager123*` (`CAREER_MANAGER`).

Para recorrer Validación de Registros, inicie sesión, seleccione `2025-2026` y
`Ciclo 2`. La cuenta de facultad ve los casos sintéticos A y B; la cuenta de carrera
ve solo el caso A. El caso A conserva una identidad coherente en Participantes,
Producción científica y Proyectos, y puede revertirse desde su historial.

## Integraciones opcionales: Dropbox y n8n

El arranque y las pruebas normales no requieren Dropbox ni n8n. El repositorio no
incluye un flujo n8n exportado: la integración se crea manualmente siguiendo
[docs/automatizacion-dropbox-n8n.md](docs/automatizacion-dropbox-n8n.md).

Si se desea probar la interfaz de n8n local, se puede iniciar además el servicio:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env up -d --build backend frontend n8n
```

n8n queda en [http://localhost:5678](http://localhost:5678). Configúrelo con secretos propios fuera del
repositorio. `DROPBOX_CLIENT_ID`, `DROPBOX_CLIENT_SECRET`, `DROPBOX_REFRESH_TOKEN` y
`N8N_WEBHOOK_URL` permanecen vacíos en `.env.example`.

- Sin las tres credenciales Dropbox, la operación correspondiente responde
  `Faltan las credenciales de Dropbox en el backend.`
- Sin `N8N_WEBHOOK_URL`, las alertas no realizan una llamada externa y responden
  `skipped`.
- MinIO sí forma parte del entorno local de evidencias.

## Documentos de referencia opcionales

`backend/data/reference/base_investigadores_fca.xlsx` es un enriquecimiento opcional,
no una dependencia de instalación, ejecución o pruebas. Si el archivo predeterminado
no existe, el procesamiento continúa sin inventar coincidencias por nombre.
La identidad sigue dependiendo de la evidencia del documento y de las relaciones
e identificadores almacenados.

Las pruebas usan datos sintéticos reproducibles. Si se proporciona explícitamente
un archivo de referencia y falta, está corrupto o es inválido, el sistema informa
el error de configuración.

Los PDF históricos `zambrano_fci021_2025.pdf`, `job234_ginfaes.pdf` y
`job242_tributaria.pdf` tampoco son requisitos del producto. Los casos que dependen
de material histórico externo se omiten explícitamente; la cobertura pública usa
datos sintéticos incluidos en el repositorio. La
[guía técnica](docs/prototype-handoff.md) amplía estos criterios.

## Cómo ejecutar las pruebas

### Backend

Desde la raíz del repositorio, instale las dependencias en un entorno virtual y
levante una PostgreSQL 16 exclusiva para pruebas. La suite crea y elimina objetos;
use únicamente esta base desechable. El parámetro `application_name` habilita el
ciclo de reversión de migraciones que exige el arnés de pruebas.

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

El resultado esperado es `OK`, con cero fallos y cero errores. Las omisiones
(`skipped`) deben indicar su motivo: integraciones opcionales sin configurar o
documentos históricos externos ausentes. El número de casos depende de la versión
y de las integraciones habilitadas; una omisión en `setUpClass` puede cubrir varios
casos descubiertos. Los informes de `docs/` corresponden a sus verificaciones
históricas, no sustituyen la salida de esta ejecución.

### Frontend

Use el backend iniciado en los pasos anteriores, con los datos demo recién
cargados y antes de modificar registros o aplicar decisiones de revisión.
`npm test` incluye pruebas de integración que verifican participantes, productos
y proyectos concretos de ese conjunto inicial. Su URL predeterminada es
`http://localhost:8000/api/v1`; puede cambiarse mediante `B2A_TEST_API_URL`.

Abra otra terminal en la raíz del repositorio y ejecute los comandos en orden:

```powershell
Set-Location frontend
npm ci
npm test
npx tsc --noEmit --incremental false
npm run build
npm audit --omit=dev
```

El resultado esperado es que las pruebas, TypeScript y el build terminen sin fallos.
Las pruebas históricas de navegador tienen requisitos adicionales y no están
incluidas en `npm test`; consulte la [guía técnica](docs/prototype-handoff.md).
La revisión visual desktop se realiza sobre la aplicación en ejecución.

La auditoría de dependencias es una comprobación aparte: puede detectar
vulnerabilidades aunque las pruebas pasen. Ejecute `npm audit --omit=dev` y registre
la fecha, el commit y los resultados Critical/High antes de cada entrega. Los
[resultados de auditorías anteriores](docs/phase3a-harness-verification.md) son
históricos y no garantizan el estado actual. Revise las correcciones compatibles;
no use `npm audit fix --force`. Una actualización mayor requiere revisión y
autorización separadas.

## Detener y limpiar

Desde la raíz del repositorio, detenga la aplicación conservando los volúmenes:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env down
```

**El siguiente comando elimina los datos locales de PostgreSQL, MinIO y n8n
guardados en los volúmenes de este proyecto.** Úselo solo para un entorno
desechable, después de verificar el nombre exacto del proyecto:

```powershell
docker compose --project-name scientific-prototype-demo --env-file .env down --volumes
```

No use `docker system prune` ni `docker volume prune`.

## Estructura principal

```text
backend/
  app/                   API, modelos, esquemas de validación y servicios
    core/prototype_baseline.py  inicialización verificable de una base nueva
    migrations/versions/       migraciones y sus contratos de integridad
  scripts/init_db.py     comando de inicialización y carga demo
  tests/                 pruebas unitarias, sintéticas y de integración
frontend/
  app/                   páginas Next.js
  components/            componentes compartidos
  lib/                   cliente API y tipos
  tests/                 pruebas habituales y pruebas históricas de navegador
docs/                    guía de entrega y documentación técnica
docker-compose.yml       infraestructura local reproducible
.env.example             configuración local sin secretos reales
```

## Alcance y límites

La solución ayuda a consolidar información, revisar identidad, contrastar metas POA,
gestionar evidencias y producir reportes. No sustituye controles institucionales de
producción, backups, observabilidad, gestión centralizada de secretos ni una estrategia
de migración para bases existentes. Es una base limpia para que otros tesistas
continúen el desarrollo.
