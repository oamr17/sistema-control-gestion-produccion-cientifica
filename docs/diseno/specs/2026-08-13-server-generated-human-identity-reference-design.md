# Referencia pública generada por el servidor

## Objetivo

Permitir aprobar o corregir una identidad de persona o autor sin pedir al gestor una clave técnica.

## Diseño aprobado

- El cliente envía `canonical_identity_key: null` para aprobación o corrección cuando no existe una referencia pública previa.
- Dentro de la misma transacción del comando `apply`, el backend crea una referencia opaca y única con el formato `human:identity:<uuid4>`.
- La referencia no usa nombres, rutas, IDs internos ni heurísticas; se conserva en la decisión y la proyección como cualquier identidad canónica humana.
- Si el cliente aporta una referencia pública existente, el backend conserva la validación y comprobación de tipo actuales.
- La acción `link` sigue exigiendo una identidad existente elegida de forma explícita: generar una identidad nueva no equivale a vincular dos registros.
- La interfaz de aprobar/corregir elimina el campo manual y explica que la referencia se asignará automáticamente al confirmar.

## Límites

- Sin migraciones, cambios de modelos, datos reales, OCR, Dropbox ni almacenamiento de borradores.
- Sin agrupación por similitud de nombres.

## Pruebas

- RED/GREEN del comando público con una identidad sin clave: persiste una clave opaca, única y no basada en la entrada.
- Conservación de una clave pública aportada explícitamente.
- Formulario: no muestra el campo para aprobar/corregir y construye el payload nulo.
