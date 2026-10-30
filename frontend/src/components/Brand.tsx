import { cn } from "./ui";

/** Hexagon logo lockup — geometric mark reused from the Mantiq identity (gold on navy). */
export function Brand({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <svg viewBox="0 0 64 64" className="size-9 shrink-0" role="img" aria-label="ProcureAI">
        <path d="M32 5 55 18v28L32 59 9 46V18Z" fill="var(--navy)" stroke="var(--gold)" strokeWidth="2.6" />
        <path d="M32 18 43.5 24.5v13L32 44 20.5 37.5v-13Z" fill="var(--gold)" />
        <path d="M32 18v26M20.5 24.5 32 31l11.5-6.5" stroke="var(--navy)" strokeWidth="1.6" fill="none" opacity="0.55" />
      </svg>
      {!compact ? (
        <span className="leading-tight">
          <span className="block text-sm font-semibold tracking-wide text-ink">ProcureAI</span>
          <span className="block text-2xs uppercase tracking-[0.18em] text-ink-subtle">Mantiq</span>
        </span>
      ) : null}
    </div>
  );
}
