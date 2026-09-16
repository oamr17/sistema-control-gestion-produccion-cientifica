# Frontend Copy, Evidence Link, and Period Selection Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Aplicar los ajustes de claridad aprobados en login, bandeja y detalle sin cambiar contratos ni backend.

**Architecture:** Reutilizar el contexto global de filtros para resolver `period_id`; conservar el flujo autenticado de evidencia y su URL blob; limitar los cambios restantes a copy y metadata de Next.js.

**Tech Stack:** Next.js 14, React 18, TypeScript, Tailwind CSS, Node test runner y Playwright.

## Global Constraints

- Repositorio exclusivo: `C:\Users\OMAR\Desktop\tesis\New project`.
- Solo escritorio; no trabajo móvil nuevo.
- Sin backend, API, DB, modelos o migraciones.
- Sin rutas internas ni secretos renderizados.
- TDD RED → GREEN antes de verificar TypeScript, build y navegador.

---

### Task 1: Login y metadata en español

**Files:**
- Modify: `frontend/tests/login-page.test.mjs`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/layout.tsx`

- [ ] Agregar expectativas fallidas para logo centrado, copy eliminado y título español.
- [ ] Ejecutar la prueba y observar RED.
- [ ] Aplicar el cambio mínimo.
- [ ] Ejecutar la prueba y observar GREEN.

### Task 2: Periodo derivado de Año/Ciclo

**Files:**
- Modify: `frontend/tests/human-review-queue.test.mjs`
- Modify: `frontend/components/human-review/ReviewQueueFilters.tsx`
- Modify: `frontend/app/human-review/page.tsx`

- [ ] Agregar expectativas fallidas para ausencia del input numérico y resolución del `period_id` seleccionado.
- [ ] Ejecutar la prueba y observar RED.
- [ ] Conectar el filtro a `useGlobalFilters` y conservar `period_id` al aplicar/limpiar filtros.
- [ ] Ejecutar la prueba y observar GREEN.

### Task 3: Lenguaje y documento original

**Files:**
- Modify: `frontend/tests/human-review-detail.test.mjs`
- Modify: `frontend/tests/human-review-e2e.test.mjs`
- Modify: `frontend/components/human-review/DetectedDataPanel.tsx`
- Modify: `frontend/components/human-review/DecisionComposer.tsx`
- Modify: `frontend/components/human-review/ReviewSessionPage.tsx`
- Modify: `frontend/app/human-review/cases/[id]/page.tsx`
- Modify: `frontend/components/human-review/EvidencePanel.tsx`

- [ ] Agregar expectativas fallidas para el nuevo copy y el enlace blob autenticado.
- [ ] Ejecutar pruebas dirigidas y observar RED.
- [ ] Aplicar el cambio mínimo manteniendo el iframe y la sanitización.
- [ ] Ejecutar pruebas dirigidas y observar GREEN.

### Task 4: Verificación final de escritorio

**Files:**
- Test: `frontend/tests/login-page.test.mjs`
- Test: `frontend/tests/human-review-queue.test.mjs`
- Test: `frontend/tests/human-review-detail.test.mjs`
- Test: `frontend/tests/human-review-e2e.test.mjs`

- [ ] Ejecutar las regresiones relacionadas una vez.
- [ ] Ejecutar `npx tsc --noEmit`.
- [ ] Ejecutar `npm run build`.
- [ ] Validar con Playwright regular las rutas `/`, `/human-review` y `/human-review/cases/[id]` en escritorio.
- [ ] Confirmar 0 errores relevantes de consola/hidratación y 0 exposiciones sensibles nuevas.
