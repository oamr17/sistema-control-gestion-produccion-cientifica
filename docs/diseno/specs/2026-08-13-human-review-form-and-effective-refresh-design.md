# Diseño: formulario de revisión y actualización de datos efectivos

## Objetivo

Hacer que el formulario de validación describa con claridad los datos obligatorios y permita aliases opcionales con nombres compuestos; propagar, en el cliente, una confirmación B2B.2 exitosa hacia las vistas operativas que ya leen la proyección humana efectiva.

## Decisiones

- `aliases` sigue siendo opcional. El campo conserva el texto que la persona escribe hasta enviar el comando, por lo que se admiten espacios dentro de cada alias y se separan los aliases con comas. La normalización existente del request recorta y descarta segmentos vacíos al confirmar.
- La interfaz muestra una leyenda `* Campo obligatorio` y añade `*` solo a controles que el validador actual exige para la acción y tipo de caso. Los aliases no se marcan como obligatorios.
- Una decisión exitosa publica un evento local de datos efectivos. Participantes, Producción y Proyectos se suscriben al evento y vuelven a leer su endpoint actual cuando estén montados. No se modifica el backend, los contratos HTTP ni los datos científicos fuente.
- La actualización solo cambia una vista cuando el caso aprobado afecta los datos que dicha vista proyecta. No se crean participantes, productos o proyectos nuevos por una decisión de identidad aislada.

## Límites

- Sin migraciones, modelos, API, parser/OCR, Dropbox, n8n ni B2B.3.
- Sin autosave ni borradores persistentes.
- Los lectores del backend existentes (`ValidatedReadService`) siguen siendo la única fuente de datos efectivos.

## Pruebas

- RED/GREEN para aliases con espacios y aliases vacíos válidos.
- RED/GREEN para marcadores obligatorios y ausencia de marcador en aliases.
- RED/GREEN para refetch de Participantes, Producción y Proyectos tras el evento local.
- Regresiones focales de decisiones, detalle y vistas operativas; TypeScript y build.
