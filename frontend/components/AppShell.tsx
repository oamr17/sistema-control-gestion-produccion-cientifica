"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BarChart3, BookOpen, ClipboardCheck, FileSpreadsheet, FolderKanban, LogOut, Target, Users } from "lucide-react";

import { clearSession, getProfile } from "@/lib/auth";
import { useGlobalFilters } from "@/lib/filters";
import { useHumanReviewCapabilities } from "@/hooks/useHumanReview";
import type { B2BAction } from "@/lib/human-review";

const items = [
  { href: "/dashboard", label: "Panel", icon: BarChart3 },
  { href: "/teachers", label: "Participantes", icon: Users },
  { href: "/production", label: "Producción", icon: BookOpen },
  { href: "/projects", label: "Proyectos", icon: FolderKanban },
  { href: "/poa", label: "POA", icon: Target },
  { href: "/reports", label: "Reportes", icon: FileSpreadsheet }
];

const humanReviewItem = {
  href: "/human-review",
  label: "Validaci\u00f3n de Registros",
  icon: ClipboardCheck
};

export function canSeeHumanReviewLink(actions: readonly B2BAction[] | null | undefined) {
  return actions?.includes("view_foundations") ?? false;
}

export function shouldShowCareerFilter(role: string | null | undefined, hideCareerFilter = false) {
  return role === "FACULTY_ADMIN" && !hideCareerFilter;
}

export function AppNavigation({
  pathname,
  actions
}: {
  pathname: string;
  actions: readonly B2BAction[] | null | undefined;
}) {
  const navigationItems = canSeeHumanReviewLink(actions)
    ? [items[0], humanReviewItem, ...items.slice(1)]
    : items;

  return (
    <nav className="mt-8 space-y-2" aria-label={"Navegaci\u00f3n principal"}>
      {navigationItems.map((item) => {
        const Icon = item.icon;
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`flex items-center gap-3 rounded-[8px] px-4 py-3 text-sm font-medium transition ${
              active ? "bg-mint text-white" : "text-ink/70 hover:bg-paper"
            }`}
          >
            <Icon size={18} aria-hidden="true" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function getRoleLabel(role: string | null | undefined) {
  if (role === "FACULTY_ADMIN") return "Gestor de facultad";
  if (role === "CAREER_MANAGER") return "Gestor de carrera";
  return "Sesión activa";
}

export function AppShell({
  children,
  hideCareerFilter = false
}: {
  children: React.ReactNode;
  hideCareerFilter?: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [profile, setProfile] = useState<ReturnType<typeof getProfile>>(null);
  const capabilities = useHumanReviewCapabilities();
  const { periods, careers, yearLabel, cycle, careerId, role, setYearLabel, setCycle, setCareerId } =
    useGlobalFilters();

  useEffect(() => {
    setProfile(getProfile());
  }, []);

  function logout() {
    clearSession();
    router.push("/");
  }

  return (
    <main className="min-h-screen bg-paper">
      <aside className="fixed inset-y-0 left-0 hidden w-72 border-r border-line bg-white p-5 lg:block">
        <div className="flex h-full flex-col">
          <div className="text-center">
            <div className="flex justify-center">
              <Image
                src="/logo-fca.png"
                alt="Facultad de Ciencias Administrativas"
                width={150}
                height={90}
                priority
                className="mb-5 h-auto w-36"
              />
            </div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-mint">Investigación</p>
            <h1 className="mt-3 text-2xl font-semibold text-ink">Control científico</h1>
          </div>
          <AppNavigation pathname={pathname} actions={capabilities.data?.actions} />
          <div className="mt-auto rounded-[8px] border border-line p-4">
            <p className="text-sm font-semibold">{profile?.full_name ?? "Usuario institucional"}</p>
            <p className="mt-1 text-xs text-ink/55">{getRoleLabel(profile?.role)}</p>
            <button
              onClick={logout}
              className="focus-ring mt-4 flex w-full items-center justify-center gap-2 rounded-[8px] border border-line px-3 py-2 text-sm"
            >
              <LogOut size={16} />
              Salir
            </button>
          </div>
        </div>
      </aside>
      <section className="lg:pl-72">
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          <div className="mb-5 flex flex-col gap-3 rounded-[8px] border border-line bg-white p-4 shadow-soft md:flex-row md:items-center md:justify-end">
            <select
              className="focus-ring rounded-[8px] border border-line bg-white px-3 py-2"
              value={yearLabel}
              onChange={(event) => setYearLabel(event.target.value)}
              aria-label="Año"
            >
              {[...new Set(["2025-2026", ...periods.map((period) => period.year_label)])].map((year) => (
                <option key={year}>{year}</option>
              ))}
            </select>
            <select
              className="focus-ring rounded-[8px] border border-line bg-white px-3 py-2"
              value={cycle}
              onChange={(event) => setCycle(Number(event.target.value))}
              aria-label="Ciclo"
            >
              <option value={1}>Ciclo 1</option>
              <option value={2}>Ciclo 2</option>
            </select>
            {shouldShowCareerFilter(role, hideCareerFilter) ? (
              <select
                className="focus-ring rounded-[8px] border border-line bg-white px-3 py-2"
                value={careerId}
                onChange={(event) => setCareerId(event.target.value)}
                aria-label="Carrera"
              >
                <option value="">Todas las carreras</option>
                {careers.map((career) => (
                  <option key={career.id} value={career.id}>
                    {career.name}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          {children}
        </div>
      </section>
    </main>
  );
}
