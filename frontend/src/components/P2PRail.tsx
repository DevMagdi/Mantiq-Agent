import { Check, Circle, ChevronDown } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { Lifecycle } from "@/lib/types";

/**
 * The eleven-stage purchase-to-pay rail.
 *
 * The stage is derived by the backend from the documents themselves, so this component only
 * *renders* what the evidence says: done stages take the gold check, the current stage is ringed,
 * future stages stay muted. Arabic and English labels both come from the API, so a stage never
 * depends on a translation that a screen forgot to add.
 */
export function P2PRail({ lifecycle, compact = false }: { lifecycle: Lifecycle; compact?: boolean }) {
  const { i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const [expanded, setExpanded] = useState<string | null>(null);
  const history = new Map(lifecycle.history.map((row) => [row.stage, row]));

  return (
    <div className="p-4">
      <ol className="flex flex-wrap items-stretch gap-1.5">
        {lifecycle.stages.map((stage, index) => {
          const label = lang === "ar" ? stage.label_ar : stage.label_en;
          const event = history.get(stage.code);
          const isLast = index === lifecycle.stages.length - 1;
          const done = stage.state === "done";
          const current = stage.state === "current";
          return (
            <li key={stage.code} className="min-w-0 flex-1">
              <button
                type="button"
                onClick={() => setExpanded(expanded === stage.code ? null : stage.code)}
                aria-expanded={expanded === stage.code}
                className={cn(
                  "group flex w-full items-start gap-1.5 rounded-md border px-2 py-1.5 text-start transition-colors",
                  done && "border-[color:var(--gold-line-soft)] bg-[color:var(--gold-wash)]",
                  current && "border-gold bg-gold/15",
                  !done && !current && "border-line/70 bg-surface-raised/30",
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                    done && "border-[color:var(--gold-300)] text-[color:var(--gold-300)]",
                    current && "border-gold bg-gold/25 text-gold",
                    !done && !current && "border-line text-ink-subtle",
                  )}
                >
                  {done ? <Check className="size-2.5" aria-hidden /> : <Circle className="size-1.5 fill-current" aria-hidden />}
                </span>
                <span className="min-w-0">
                  <span
                    className={cn(
                      "block truncate text-2xs font-medium leading-tight",
                      current ? "text-ink" : done ? "text-ink" : "text-ink-subtle",
                    )}
                    title={label}
                  >
                    {label}
                  </span>
                  {!compact ? (
                    <span className="block truncate text-[0.625rem] text-ink-subtle">
                      {event?.reached_at ? dateTime(event.reached_at, lang) : lang === "ar" ? "بالانتظار" : "pending"}
                    </span>
                  ) : null}
                </span>
              </button>
              {expanded === stage.code ? (
                <div className="mt-1 rounded-md border border-line bg-surface px-2 py-1.5 text-[0.6875rem] text-ink-muted">
                  <p className="text-ink">
                    {lang === "ar" ? stage.label_ar : stage.label_en}
                    {current ? <span className="ms-1 text-gold">· {lang === "ar" ? "المرحلة الحالية" : "current"}</span> : null}
                  </p>
                  {event ? (
                    <p className="mt-0.5">
                      {dateTime(event.reached_at, lang)} · {event.source.replaceAll("_", " ")}
                    </p>
                  ) : (
                    <p className="mt-0.5">{lang === "ar" ? "لم تُبلغ بعد" : "not reached yet"}</p>
                  )}
                </div>
              ) : null}
              {!isLast && !compact ? <span className="sr-only">→</span> : null}
            </li>
          );
        })}
      </ol>
      <div className="mt-3 flex items-start gap-2 rounded-md border border-[color:var(--gold-line-soft)] bg-[color:var(--gold-wash)] px-2.5 py-2">
        <ChevronDown className="mt-0.5 size-3.5 shrink-0 rotate-[-90deg] text-gold" aria-hidden />
        <p className="text-2xs text-ink">
          <span className="font-semibold text-gold">{lang === "ar" ? "الخطوة التالية: " : "Next: "}</span>
          {lang === "ar" ? lifecycle.next_action_ar : lifecycle.next_action_en}
        </p>
      </div>
    </div>
  );
}
