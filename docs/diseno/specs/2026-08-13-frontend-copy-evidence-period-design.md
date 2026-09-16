# Ajustes de claridad en login y validación

## Objetivo

Simplificar la interfaz de escritorio sin cambiar contratos HTTP, reglas científicas ni persistencia.

## Diseño aprobado

- Centrar el logotipo del formulario de acceso.
- Eliminar los textos «Ingreso institucional» y «Gestión de KPI, evidencias, POA y reportes por carrera».
- Cambiar el título del navegador a «Control científico — FCA» y usar una descripción en español.
- Eliminar el campo numérico «Periodo» de la bandeja. La consulta usará automáticamente el `period_id` que corresponda a los selectores globales Año y Ciclo 1/2.
- Sustituir «Overrides vigentes» por «Ajustes aplicados» y su estado vacío por «No hay ajustes aplicados».
- Sustituir «Decisión científica» por «Decisión».
- Sustituir «Validación humana guiada» por «Validación humana» y eliminar «guiada» del subtítulo del detalle.
- Mantener el visor PDF. Cuando exista evidencia cargada, incluir el enlace «Abrir documento original en una pestaña nueva» usando exclusivamente la URL blob autenticada. Cuando el backend indique que no existe evidencia, mostrar el nombre público conocido y un estado claro, sin enlace roto.

## Límites

- Solo escritorio; no se realiza trabajo específico móvil.
- Sin cambios backend, API, base de datos, modelos o migraciones.
- Sin exponer rutas internas, claves, localizadores o identificadores técnicos.
- El filtro sigue enviando el `period_id` público vigente; solo cambia su selección visual.

## Verificación

- TDD con pruebas de login, bandeja y detalle.
- TypeScript y build de Next.js.
- QA Playwright de escritorio en login, bandeja y detalle.
- Consola, hidratación y overflow sin errores nuevos.
