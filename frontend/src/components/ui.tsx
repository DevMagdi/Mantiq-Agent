import * as Dialog from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { clsx, type ClassValue } from "clsx";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/* ------------------------------------------------------------------ feedback */

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin", className)} aria-hidden />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} aria-hidden />;
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="flex size-12 items-center justify-center rounded-full border border-line bg-surface-raised text-gold">
        {icon ?? <Check className="size-5" aria-hidden />}
      </div>
      <div>
        <p className="font-medium text-ink">{title}</p>
        {description ? <p className="mt-1 max-w-md text-sm text-ink-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-full border border-line bg-surface-raised text-[color:var(--danger-ink)]">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <p className="max-w-lg text-sm text-ink">{message}</p>
      {onRetry ? (
        <button type="button" className="btn-secondary" onClick={onRetry}>
          {`Retry`}
        </button>
      ) : null}
    </div>
  );
}

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" aria-busy="true">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className={index === 0 ? "h-6 w-1/3" : undefined} />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ status */

export type Tone = "neutral" | "gold" | "ok" | "warn" | "danger" | "info";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-line text-ink-muted",
  gold: "border-gold/60 bg-gold/10 text-[color:var(--gold-300)]",
  ok: "border-[color:var(--ok)]/40 bg-[color:var(--ok)]/10 text-[color:var(--ok-ink)]",
  warn: "border-[color:var(--warn)]/40 bg-[color:var(--warn)]/10 text-[color:var(--warn-ink)]",
  danger: "border-[color:var(--danger)]/40 bg-[color:var(--danger)]/10 text-[color:var(--danger-ink)]",
  info: "border-[color:var(--info)]/40 bg-[color:var(--info)]/10 text-[color:var(--info-ink)]",
};

const STATUS_TONE: Record<string, Tone> = {
  draft: "neutral",
  clarifying: "warn",
  ready: "info",
  rfq_created: "ok",
  cancelled: "danger",
  sent: "info",
  replied: "gold",
  negotiating: "gold",
  closed: "ok",
  pending: "warn",
  queued: "neutral",
  approved: "ok",
  rejected: "danger",
  revision_requested: "warn",
  parsed: "info",
  needs_review: "warn",
  verified: "ok",
  failed: "danger",
  blacklisted: "danger",
  preferred: "gold",
  active: "ok",
  paused: "warn",
  blocked: "danger",
  send: "ok",
  draft_ok: "gold",
  /* post-award lifecycle */
  issued: "info",
  in_transit: "info",
  partially_received: "warn",
  received: "gold",
  confirmed: "ok",
  matched: "ok",
  mismatched: "danger",
  mismatch: "danger",
  incomplete: "warn",
  approved_for_payment: "ok",
  partially_paid: "warn",
  paid: "ok",
  disputed: "danger",
  settled: "ok",
  overdue: "danger",
  outstanding: "warn",
};

export function StatusChip({ status, label }: { status: string; label?: string }) {
  const tone = STATUS_TONE[status] ?? "neutral";
  return <span className={cn("chip", TONE_CLASS[tone])}>{label ?? status.replaceAll("_", " ")}</span>;
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cn("chip", TONE_CLASS[tone])}>{children}</span>;
}

/* ------------------------------------------------------------------ layout */

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cn("card", className)}>{children}</section>;
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="card-header">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p> : null}
      </div>
      {action}
    </header>
  );
}

export function Kpi({
  label,
  value,
  hint,
  trend,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  trend?: { value: number; label: string };
  icon?: ReactNode;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
        {icon ? <span className="text-gold">{icon}</span> : null}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular text-ink">{value}</p>
      <div className="mt-1 flex items-center gap-2 text-xs">
        {trend ? (
          <span className={trend.value >= 0 ? "text-[color:var(--ok-ink)]" : "text-[color:var(--danger-ink)]"}>
            {trend.value >= 0 ? "▲" : "▼"} {Math.abs(trend.value).toFixed(1)}%
          </span>
        ) : null}
        {hint ? <span className="text-ink-subtle">{hint}</span> : null}
      </div>
    </div>
  );
}

export function Progress({ value, tone = "gold" }: { value: number; tone?: Tone }) {
  const color =
    tone === "ok" ? "var(--ok)" : tone === "danger" ? "var(--danger)" : tone === "warn" ? "var(--warn)" : "var(--gold)";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={value}>
      <div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: color }} />
    </div>
  );
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-full border border-gold/40 bg-gold/15 text-xs font-semibold text-[color:var(--gold-300)]",
        className,
      )}
      aria-hidden
    >
      {initials || "?"}
    </span>
  );
}

/* ------------------------------------------------------------------ overlays */

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "max-w-xl",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 end-0 z-50 flex w-full flex-col border-s border-line bg-surface-alt shadow-pop",
            width,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <Dialog.Title className="text-base font-semibold text-ink">{title}</Dialog.Title>
              {description ? (
                <Dialog.Description className="mt-1 text-sm text-ink-muted">{description}</Dialog.Description>
              ) : null}
            </div>
            <Dialog.Close asChild>
              <button type="button" className="btn-ghost" aria-label="Close">
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? <div className="border-t border-line px-5 py-3">{footer}</div> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabList({ items, className }: { items: { value: string; label: string }[]; className?: string }) {
  return (
    <TabsPrimitive.List className={cn("flex flex-wrap gap-1 border-b border-line pb-1", className)}>
      {items.map((item) => (
        <TabsPrimitive.Trigger
          key={item.value}
          value={item.value}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm text-ink-muted transition-colors",
            "hover:text-ink data-[state=active]:bg-surface-raised data-[state=active]:text-gold",
          )}
        >
          {item.label}
        </TabsPrimitive.Trigger>
      ))}
    </TabsPrimitive.List>
  );
}

export function TabPanel({ value, children }: { value: string; children: ReactNode }) {
  return (
    <TabsPrimitive.Content value={value} className="pt-4 focus-visible:outline-none">
      {children}
    </TabsPrimitive.Content>
  );
}

export function Hint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={250}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            sideOffset={6}
            className="z-50 max-w-xs rounded-md border border-line bg-surface-raised px-2 py-1 text-xs text-ink shadow-pop"
          >
            {text}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; message: string; tone: "ok" | "danger" | "info" };
type ToastContextValue = { push: (message: string, tone?: Toast["tone"]) => void };
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const push = useCallback((message: string, tone: Toast["tone"] = "info") => {
    counter.current += 1;
    const id = counter.current;
    setToasts((current) => [...current, { id, message, tone }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 5_000);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 end-4 z-[60] flex w-80 flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-pop backdrop-blur",
              toast.tone === "ok"
                ? "border-[color:var(--ok)]/40 bg-[color:var(--ok)]/15 text-ink"
                : toast.tone === "danger"
                  ? "border-[color:var(--danger)]/40 bg-[color:var(--danger)]/15 text-ink"
                  : "border-line bg-surface-raised/95 text-ink",
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

/** Escape-to-close hook for custom overlays. */
export function useEscape(onEscape: () => void, active = true) {
  useEffect(() => {
    if (!active) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onEscape();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onEscape, active]);
}
