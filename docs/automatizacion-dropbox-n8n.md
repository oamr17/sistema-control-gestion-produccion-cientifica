# Automatización opcional Dropbox + n8n + Backend

Esta guía describe una integración **opcional**. El frontend, backend, PostgreSQL,
MinIO, bootstrap y suite pública funcionan sin n8n ni Dropbox. El repositorio no
incluye un export de workflow: si se desea demostrar la integración, debe crearse
manualmente con los nodos descritos aquí y con credenciales propias no versionadas.

## Objetivo

Dejar la automatizacion en una forma estable y escalable:

- `n8n` solo lista y filtra archivos en Dropbox.
- `n8n` envia al backend una sola carga JSON con los PDF detectados.
- El `backend` descarga cada PDF directamente desde Dropbox.
- El `backend` intenta extraer texto normal y, si no puede, usa OCR local.
- Si un PDF falla, se registra como omitido o con error sin detener todo el lote.

Con esto evitamos:

- descargar PDF por PDF dentro de n8n;
- errores por timeout o `aborted` en lotes medianos;
- tener que excluir manualmente archivos uno por uno;
- duplicar logica de autenticacion en muchos nodos.

---

## Variables necesarias

Solo para habilitar esta integración, configure en su archivo local `.env`:

```env
DROPBOX_CLIENT_ID=
DROPBOX_CLIENT_SECRET=
DROPBOX_REFRESH_TOKEN=
INGEST_API_KEY=<clave-local-no-versionada>
```

El `docker-compose.yml` ya pasa estas variables al backend.

---

## Endpoint final

El flujo de n8n ya no debe enviar binarios PDF al backend.

Ahora debe enviar un solo `POST` a:

```text
http://backend:8000/api/v1/imports/progress-pdf-batch
```

con un cuerpo JSON como este:

```json
{
  "files": [
    {
      "path_lower": "/informes semestrales/julio 2025-enero-julio 2025 ci2526/...",
      "path_display": "/INFORMES SEMESTRALES/Julio 2025-Enero-Julio 2025 CI2526/...",
      "name": "archivo.pdf"
    }
  ]
}
```

---

## Flujo final en n8n

### 1. `Schedule Trigger`

Configuracion sugerida:

- intervalo: `Days`
- cada: `1`

Solo sirve para disparar la revision automatica.

---

### 2. `List a folder`

Sirve para traer las 3 carpetas principales dentro de `INFORMES SEMESTRALES`.

---

### 3. `HTTP Request`

Este nodo consulta Dropbox para listar el contenido interno de cada carpeta principal.

Configuracion:

- metodo: `POST`
- URL:

```text
https://api.dropboxapi.com/2/files/list_folder
```

- autenticacion:
  - `Predefined Credential Type`
  - `Dropbox OAuth2 API`
  - credencial: tu credencial Dropbox local

- `Send Body`: activado
- `Body Content Type`: `JSON`
- `Specify Body`: `Using JSON`

JSON:

```json
{
  "path": "={{ $json[\"pathLower\"] || $json[\"path_lower\"] || $json[\"pathDisplay\"] || $json[\"path_display\"] }}",
  "recursive": true,
  "include_deleted": false,
  "include_has_explicit_shared_members": false,
  "include_mounted_folders": true,
  "include_non_downloadable_files": true
}
```

---

### 4. `Code in JavaScript`

Este nodo aplana `entries`.

Codigo:

```javascript
const salida = [];

for (const item of items) {
  const entradas = item.json.entries || [];

  for (const entrada of entradas) {
    salida.push({ json: entrada });
  }
}

return salida;
```

---

### 5. `Code in JavaScript1`

Filtra solo archivos.

Codigo:

```javascript
return items.filter((item) => {
  const tipo = String(item.json[".tag"] || "").toLowerCase();
  return tipo === "file";
});
```

---

### 6. `Code in JavaScript2`

Excluye archivos que no son el informe util.

Codigo:

```javascript
return items.filter((item) => {
  const nombre = String(item.json.name || "").toLowerCase();

  const esCorreo =
    nombre.includes("bandeja de entrada") ||
    nombre.includes("outlook") ||
    nombre.includes("correo");

  return !esCorreo;
});
```

---

### 7. `Code in JavaScript3`

Filtra solo PDF.

Codigo:

```javascript
return items.filter((item) => {
  const nombre = String(item.json.name || "").toLowerCase();
  return nombre.endsWith(".pdf");
});
```

---

### 8. `Code in JavaScript4`

Este es el cambio clave.

Ya no devuelve uno por uno para descargar.
Ahora arma un unico lote para el backend.

Codigo:

```javascript
return [
  {
    json: {
      files: items.map((item) => ({
        path_lower: item.json.path_lower || item.json.pathLower || null,
        path_display: item.json.path_display || item.json.pathDisplay || null,
        name: item.json.name || null,
      })),
    },
  },
];
```

---

### 9. `HTTP Enviar Backend`

Configuracion:

- metodo: `POST`
- URL:

```text
http://backend:8000/api/v1/imports/progress-pdf-batch
```

- autenticacion: `None`
- `Send Headers`: activado
- encabezado:

```text
x-api-key
```

valor:

```text
<mismo-valor-local-de-INGEST_API_KEY>
```

Use el valor de su `.env`; nunca lo copie a documentación, capturas o exports que se
vayan a versionar.

- `Send Body`: activado
- `Body Content Type`: `JSON`
- `Specify Body`: `Using JSON`

JSON:

```javascript
={{ $json }}
```

---

## Nodos que debes eliminar del flujo final

Ya no necesitas:

- `Loop Over Items`
- `HTTP Request1` para descargar PDF desde Dropbox
- envio binario PDF a PDF

Eso ahora lo hace el backend internamente.

---

## Por que esta arquitectura es mejor

### Antes

- n8n descargaba cada PDF
- n8n enviaba cada PDF al backend
- si habia 15 o 20 archivos, aparecian errores como `aborted`
- habia demasiado trafico binario dentro de n8n

### Ahora

- n8n solo manda metadatos
- el backend descarga desde Dropbox con refresh token
- el backend controla OCR, errores y omisiones
- un PDF malo no rompe toda la corrida
- escala mucho mejor

---

## Que pasa con PDFs problematicos

El backend sigue esta logica:

1. intenta extraer texto embebido;
2. si no puede, intenta OCR local;
3. si aun asi falla, marca el archivo como omitido o con error;
4. sigue con los demas archivos.

Asi no se pierde la corrida completa por un solo archivo.

---

## Levantar nuevamente el proyecto

Después de cambiar variables o backend, desde la raíz de su propia clonación:

```powershell
Set-Location <ruta-del-repositorio>
docker compose --project-name scientific-prototype-demo --env-file .env down
docker compose --project-name scientific-prototype-demo --env-file .env up -d --build backend frontend n8n
```

---

## Resultado esperado

Cuando ejecutes el flujo:

- `n8n` debe terminar con un solo `HTTP Enviar Backend`
- el backend debe procesar el lote completo
- la respuesta debe devolverte una lista de resultados por archivo
- algunos podran salir como `SUCCESS`
- otros podrian salir como `SKIPPED` o `ERROR` sin detener la corrida
