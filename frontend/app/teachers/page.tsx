"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { api } from "@/lib/api";
import { useCachedQuery, useDataCache } from "@/lib/data-cache";
import { subscribeEffectiveDataRefresh } from "@/lib/effective-data-refresh";
import { useGlobalFilters } from "@/lib/filters";
import type { Participant } from "@/lib/types";

export default function TeachersPage() {
  return (
    <Suspense fallback={null}>
      <CanonicalParticipantsPage />
    </Suspense>
  );
}

function CanonicalParticipantsPage() {
  const { effectiveCareerId } = useGlobalFilters();
  const cache = useDataCache();
  const searchParams = useSearchParams();
  const externalUniversity = searchParams.get("external_university");
  const reviewBucket = searchParams.get("review_bucket");
  const typeFilter = searchParams.get("type");
  const researchOnly = searchParams.get("research") === "true";
  const participantsQuery = useCachedQuery<Participant[]>(
    `canonical-participants:${effectiveCareerId || "all"}`,
    () => api.participants(effectiveCareerId ? `?career_id=${effectiveCareerId}` : "")
  );

  useEffect(() => subscribeEffectiveDataRefresh(() => {
    cache.invalidate("canonical-participants:");
  }), [cache]);

  const allRows = participantsQuery.data ?? [];
  const rows = allRows.filter((item) => {
    if (externalUniversity && !item.affiliation.toLocaleLowerCase().includes(externalUniversity.toLocaleLowerCase())) return false;
    if (reviewBucket && item.review_bucket !== reviewBucket) return false;
    if (typeFilter && item.person_type !== typeFilter) return false;
    if (researchOnly && item.research_entities.length === 0 && item.participation_count === 0) return false;
    return true;
  });
  const participationCount = allRows.reduce((total, item) => total + item.participation_count, 0);
  const roleCount = allRows.reduce((total, item) => total + item.role_count, 0);
  const authorshipCount = allRows.reduce((total, item) => total + item.authorship_count, 0);
  const externalRows = allRows.filter((item) => item.person_type === "investigador_externo");
  const externalValidated = externalRows.filter((item) => item.kpi_eligible).length;

  return (
    <AppShell>
      <header>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-mint">Identidad institucional</p>
        <h2 className="mt-2 text-3xl font-semibold">Personas únicas</h2>
      </header>

      <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["Personas únicas", allRows.length],
          ["Participaciones", participationCount],
          ["Roles", roleCount],
          ["Autorías", authorshipCount]
        ].map(([label, value]) => (
          <article key={label} className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
            <p className="text-sm text-ink/60">{label}</p>
            <p className="mt-1 text-2xl font-semibold text-ink">{value}</p>
          </article>
        ))}
      </section>

      <section className="mt-3 grid gap-3 sm:grid-cols-3">
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos detectados</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{externalRows.length}</p>
        </article>
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos validados KPI</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{externalValidated}</p>
        </article>
        <article className="rounded-[8px] border border-line bg-white p-4 shadow-soft">
          <p className="text-sm text-ink/60">Externos pendientes</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{externalRows.length - externalValidated}</p>
        </article>
      </section>

      {participantsQuery.isInitialLoading ? (
        <p className="mt-6 rounded-[8px] border border-line bg-white p-4 text-sm text-ink/60 shadow-soft">Cargando identidades...</p>
      ) : null}

      <section className="mt-6">
        <DataTable
          rows={rows}
          pageSize={10}
          columns={[
            {
              key: "canonical_name",
              label: "Persona",
              render: (item) => (
                <div>
                  <p>{item.canonical_name}</p>
                  {item.possible_match_notice ? (
                    <p className="mt-1 text-xs font-semibold text-amber-700 lg:hidden">{item.possible_match_notice}</p>
                  ) : null}
                </div>
              )
            },
            { key: "type_label", label: "Tipo" },
            { key: "roles", label: "Roles", render: (item) => item.role_labels.join(", ") || "Sin rol" },
            { key: "documents", label: "Documentos", render: (item) => item.documents.length },
            { key: "authorships", label: "Autorías", render: (item) => item.authorship_count },
            {
              key: "overall_status",
              label: "Estado",
              render: (item) => (
                <div className="space-y-1">
                  <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${item.kpi_eligible ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"}`}>
                    {item.kpi_eligible ? "Validada" : "Pendiente"}
                  </span>
                  {item.possible_match_notice ? (
                    <p className="text-xs font-semibold text-amber-700">{item.possible_match_notice}</p>
                  ) : null}
                </div>
              )
            }
          ]}
          renderExpanded={(item) => (
            <div className="space-y-5">
              <div className="grid gap-4 lg:grid-cols-3">
                <article className="rounded-[8px] border border-line bg-white p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">Identidad canónica</p>
                  <p className="mt-2 text-sm font-semibold text-ink">{item.canonical_name}</p>
                  <p className="mt-1 break-all text-xs text-ink/55">{item.canonical_identity_key}</p>
                </article>
                <article className="rounded-[8px] border border-line bg-white p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">Afiliación</p>
                  <p className="mt-2 text-sm text-ink/75">{item.affiliation}</p>
                  <p className="mt-1 text-xs text-ink/55">Confianza: {item.identity_confidence == null ? "No registrada" : `${Math.round(item.identity_confidence * 100)}%`}</p>
                </article>
                <article className="rounded-[8px] border border-line bg-white p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">Estado</p>
                  <p className="mt-2 text-sm text-ink/75">{item.kpi_eligible ? "Elegible KPI" : "Pendiente de revisión"}</p>
                  <p className="mt-1 text-xs text-ink/55">{item.pending_reasons[0] ?? item.identity_reason ?? "Sin observaciones"}</p>
                </article>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <article className="rounded-[8px] border border-line bg-white p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">Autorías específicas</p>
                  {item.authorships.length ? (
                    <ul className="mt-2 space-y-2 text-sm text-ink/75">
                      {item.authorships.map((authorship) => <li key={authorship.production_id}>{authorship.title}</li>)}
                    </ul>
                  ) : <p className="mt-2 text-sm text-ink/60">Sin autorías explícitas</p>}
                </article>
                <article className="rounded-[8px] border border-line bg-white p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">Investigaciones y entidades</p>
                  {item.research_entities.length ? (
                    <ul className="mt-2 space-y-2 text-sm text-ink/75">
                      {item.research_entities.map((entity) => <li key={entity.id}>{entity.name ?? entity.type}</li>)}
                    </ul>
                  ) : <p className="mt-2 text-sm text-ink/60">Sin entidad relacionada</p>}
                </article>
              </div>

              {item.possible_matches.length ? (
                <section>
                  <h3 className="text-sm font-semibold text-ink">Posibles coincidencias informativas</h3>
                  <div className="mt-2 grid gap-3 lg:grid-cols-2">
                    {item.possible_matches.map((match) => (
                      <article key={match.canonical_identity_key} className="rounded-[8px] border border-amber-200 bg-amber-50 p-3">
                        <p className="text-sm font-semibold text-ink">{match.canonical_name}</p>
                        <p className="mt-1 break-all text-xs text-ink/55">{match.canonical_identity_key}</p>
                        <p className="mt-2 text-sm text-ink/75">Documento: {match.document ?? "No registrado"}</p>
                        <p className="mt-1 text-sm text-ink/75">Evidencia: {match.evidence_type}</p>
                        <p className="mt-1 text-sm text-ink/75">Roles: {match.roles.join(", ")}</p>
                        <p className="mt-2 text-xs text-ink/60">{match.reason_not_merged}</p>
                      </article>
                    ))}
                  </div>
                </section>
              ) : null}

              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-line text-sm">
                  <thead className="bg-white">
                    <tr>
                      <th className="px-3 py-2 text-left font-semibold text-ink/70">Variante</th>
                      <th className="px-3 py-2 text-left font-semibold text-ink/70">Origen</th>
                      <th className="px-3 py-2 text-left font-semibold text-ink/70">Documento</th>
                      <th className="px-3 py-2 text-left font-semibold text-ink/70">Página</th>
                      <th className="px-3 py-2 text-left font-semibold text-ink/70">Estado fuente</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {item.variants.map((variant) => (
                      <tr key={`${variant.source_table}-${variant.source_id}`}>
                        <td className="px-3 py-2 text-ink/75">{variant.raw_name ?? variant.normalized_name}</td>
                        <td className="px-3 py-2 text-ink/75">{variant.source_table === "person_roles" ? "Rol" : "Autoría"}</td>
                        <td className="px-3 py-2 text-ink/75">{variant.document ?? "No registrado"}</td>
                        <td className="px-3 py-2 text-ink/75">{variant.source_page ?? "No registrada"}</td>
                        <td className="px-3 py-2 text-ink/75">{variant.validation_status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        />
      </section>
    </AppShell>
  );
}
