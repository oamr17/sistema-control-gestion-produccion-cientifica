import { LucideIcon } from "lucide-react";
import Link from "next/link";

type StatCardProps = {
  label: string;
  value: string;
  helper: string;
  note?: string;
  icon: LucideIcon;
  accent: "mint" | "coral" | "amber" | "grape";
  href?: string;
};

const accentMap = {
  mint: "bg-mint/10 text-mint",
  coral: "bg-coral/10 text-coral",
  amber: "bg-amber/25 text-[#705500]",
  grape: "bg-grape/30 text-ink"
};

export function StatCard({ label, value, helper, note, icon: Icon, accent, href }: StatCardProps) {
  const content = (
    <>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-ink/60">{label}</p>
          <strong className="mt-2 block text-3xl font-semibold tracking-normal">{value}</strong>
        </div>
        <span className={`rounded-[8px] p-3 ${accentMap[accent]}`}>
          <Icon size={22} />
        </span>
      </div>
      <p className="mt-4 text-sm text-ink/55">{helper}</p>
      {note ? <p className="mt-2 text-xs text-ink/45">{note}</p> : null}
    </>
  );

  const className =
    "block rounded-[8px] border border-line bg-white p-5 shadow-soft transition hover:-translate-y-0.5 hover:border-mint/50 hover:shadow-md";

  if (href) {
    return (
      <Link href={href} className={className}>
        {content}
      </Link>
    );
  }

  return (
    <article className={className}>
      {content}
    </article>
  );
}
