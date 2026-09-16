"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { ReviewSessionPage } from "@/components/human-review/ReviewSessionPage";

export default function HumanReviewCasePage({ params }: { params: { id: string } }) {
  const caseId = typeof params.id === "string" && params.id.trim() ? params.id : null;

  return (
    <AppShell hideCareerFilter>
      <header className="mb-6">
        <Link href="/human-review" className="focus-ring inline-flex items-center gap-2 rounded-[8px] text-sm font-semibold text-ink/65 hover:text-ink">
          <ArrowLeft size={17} aria-hidden="true" /> Volver a la bandeja
        </Link>
        <p className="mt-5 text-sm font-semibold uppercase tracking-[0.18em] text-mint">Control científico</p>
        <h2 className="mt-2 text-3xl font-semibold text-ink">Detalle del caso</h2>
        <p className="mt-2 max-w-2xl text-sm text-ink/60">Evidencia y validación de revisiones relacionadas.</p>
      </header>
      <ReviewSessionPage anchorCaseId={caseId} />
    </AppShell>
  );
}
