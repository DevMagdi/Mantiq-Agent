import { DndContext, DragOverlay, PointerSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, GripVertical, LayoutGrid, List, Plus, Send, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import { Card, Drawer, EmptyState, ErrorState, LoadingBlock, Spinner, StatusChip, cn, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num, relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { Board, Page, PurchaseRequest, RFQSummary, Supplier } from "@/lib/types";
import type { ColumnDef } from "@tanstack/react-table";

const COLUMNS = ["draft", "sent", "replied", "negotiating", "closed"] as const;
type ColumnKey = (typeof COLUMNS)[number];

export default function RfqBoardPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<"board" | "list">("board");
  const [newOpen, setNewOpen] = useState(false);
  const [dragging, setDragging] = useState<RFQSummary | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const board = useQuery({
    queryKey: ["board"],
    queryFn: () => api.get<Board>("/api/v1/rfqs/board"),
  });

  const list = useQuery({
    queryKey: ["rfqs"],
    queryFn: () => api.get<Page<RFQSummary>>("/api/v1/rfqs?page_size=100"),
  });

  const transition = useMutation({
    mutationFn: async ({ rfq, target }: { rfq: RFQSummary; target: ColumnKey }) => {
      if (target === "sent" && rfq.status === "draft") {
        return api.post(`/api/v1/rfqs/${rfq.id}/dispatch`);
      }
      if (target === "closed") {
        return api.post(`/api/v1/rfqs/${rfq.id}/close`, { reason: "Closed from the board" });
      }
      throw new Error(
        target === "negotiating"
          ? "Open the RFQ to draft a counter-offer under policy."
          : "This transition needs offers or a human decision — open the RFQ.",
      );
    },
    onSuccess: async () => {
      toast.push("RFQ updated", "ok");
      await queryClient.invalidateQueries({ queryKey: ["board"] });
      await queryClient.invalidateQueries({ queryKey: ["rfqs"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
    onSettled: () => setDragging(null),
  });

  const columns = useMemo(() => board.data?.columns ?? {}, [board.data]);

  const onDragEnd = (event: DragEndEvent) => {
    setDragging(null);
    const target = event.over?.id as ColumnKey | undefined;
    const rfq = event.active.data.current?.rfq as RFQSummary | undefined;
    if (!target || !rfq || target === rfq.status) return;
    transition.mutate({ rfq, target });
  };

  const tableColumns: ColumnDef<RFQSummary, unknown>[] = [
    { accessorKey: "code", header: "RFQ", cell: ({ row }) => <span className="font-mono text-xs text-gold">{row.original.code}</span> },
    {
      accessorKey: "title",
      header: t("rfqs.title"),
      cell: ({ row }) => (
        <div className="max-w-md">
          <p className="truncate">{row.original.title}</p>
          <p className="text-2xs text-ink-subtle">{`${row.original.mode} · ${relative(row.original.deadline, lang)}`}</p>
        </div>
      ),
    },
    { accessorKey: "status", header: t("common.status"), cell: ({ row }) => <StatusChip status={row.original.status} /> },
    { accessorKey: "currency", header: t("common.currency"), cell: ({ row }) => <span className="text-xs">{row.original.currency}</span> },
    {
      accessorKey: "estimated_value",
      header: t("common.total"),
      cell: ({ row }) => <span className="tabular">{row.original.estimated_value ? money(row.original.estimated_value, row.original.currency, lang) : "—"}</span>,
    },
    { accessorKey: "deadline", header: t("common.deadline"), cell: ({ row }) => <span className="tabular text-xs">{date(row.original.deadline, lang)}</span> },
  ];

  return (
    <div className="space-y-4">
      <PageHeading
        title={t("rfqs.title")}
        subtitle={t("rfqs.subtitle")}
        actions={
          <>
            <div className="segmented" role="group" aria-label={t("rfqs.view")}>
              <button
                type="button"
                className="segmented-item"
                onClick={() => setView("board")}
                aria-pressed={view === "board"}
                aria-label={t("rfqs.viewBoard")}
                title={t("rfqs.viewBoard")}
              >
                <LayoutGrid className="size-3.5" aria-hidden />
              </button>
              <button
                type="button"
                className="segmented-item"
                onClick={() => setView("list")}
                aria-pressed={view === "list"}
                aria-label={t("rfqs.viewList")}
                title={t("rfqs.viewList")}
              >
                <List className="size-3.5" aria-hidden />
              </button>
            </div>
            <button type="button" className="btn-primary" onClick={() => setNewOpen(true)}>
              <Plus className="size-4" aria-hidden /> {t("rfqs.newRfq")}
            </button>
          </>
        }
      />

      {board.isLoading || list.isLoading ? (
        <LoadingBlock rows={6} />
      ) : board.error || list.error ? (
        <ErrorState message={errorMessage(board.error ?? list.error)} onRetry={() => void board.refetch()} />
      ) : view === "board" ? (
        <DndContext sensors={sensors} onDragStart={(event) => setDragging((event.active.data.current?.rfq as RFQSummary) ?? null)} onDragEnd={onDragEnd} onDragCancel={() => setDragging(null)}>
          <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
            {COLUMNS.map((column) => (
              <BoardColumn key={column} column={column} label={t(`rfqs.columns.${column}`)} items={columns[column] ?? []} onOpen={(id) => navigate(`/rfqs/${id}`)} />
            ))}
          </div>
          <DragOverlay>{dragging ? <RfqCard rfq={dragging} dragging /> : null}</DragOverlay>
        </DndContext>
      ) : (
        <Card className="overflow-hidden">
          <DataTable data={list.data?.items ?? []} columns={tableColumns} onRowClick={(row) => navigate(`/rfqs/${row.id}`)} empty={<EmptyState title={t("common.empty")} />} />
        </Card>
      )}

      <NewRfqDrawer
        open={newOpen || Boolean(params.get("pr"))}
        preselectedPr={params.get("pr")}
        onClose={() => {
          setNewOpen(false);
          params.delete("pr");
          setParams(params, { replace: true });
        }}
        onCreated={(id) => {
          setNewOpen(false);
          params.delete("pr");
          setParams(params, { replace: true });
          void queryClient.invalidateQueries({ queryKey: ["board"] });
          navigate(`/rfqs/${id}`);
        }}
      />
    </div>
  );
}

function BoardColumn({ column, label, items, onOpen }: { column: ColumnKey; label: string; items: RFQSummary[]; onOpen: (id: string) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: column });
  return (
    <section ref={setNodeRef} className={cn("flex min-h-40 flex-col gap-2 rounded-lg border border-line bg-surface-alt/60 p-2", isOver && "border-gold/70 bg-gold/5")} aria-label={label}>
      <header className="flex items-center justify-between px-1 py-0.5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{label}</h2>
        <span className="chip">{num(items.length)}</span>
      </header>
      <div className="flex flex-col gap-2">
        {items.map((rfq) => (
          <DraggableCard key={rfq.id} rfq={rfq} onOpen={onOpen} />
        ))}
        {items.length === 0 ? <p className="px-1 py-6 text-center text-2xs text-ink-subtle">—</p> : null}
      </div>
    </section>
  );
}

function DraggableCard({ rfq, onOpen }: { rfq: RFQSummary; onOpen: (id: string) => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: rfq.id, data: { rfq } });
  return (
    <div
      ref={setNodeRef}
      style={transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined}
      className={cn("touch-none", isDragging && "opacity-40")}
    >
      <div className="group relative">
        <RfqCard rfq={rfq} onOpen={() => onOpen(rfq.id)} />
        <button
          type="button"
          className="absolute end-1 top-1 cursor-grab text-ink-subtle opacity-0 transition-opacity group-hover:opacity-100"
          aria-label="Move"
          {...attributes}
          {...listeners}
        >
          <GripVertical className="size-4" />
        </button>
      </div>
    </div>
  );
}

function RfqCard({ rfq, onOpen, dragging = false }: { rfq: RFQSummary; onOpen?: () => void; dragging?: boolean }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  return (
    <article className={cn("card p-0 transition-shadow hover:shadow-pop", dragging && "shadow-pop")}>
      <button
        type="button"
        className="block w-full p-2.5 text-start"
        onClick={onOpen}
        disabled={!onOpen}
        aria-label={`${rfq.code} ${rfq.title}`}
      >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-2xs text-gold">{rfq.code}</span>
        <StatusChip status={rfq.status} />
      </div>
      <p className="mt-1 line-clamp-2 text-sm text-ink">{rfq.title}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs text-ink-subtle">
        <span className="inline-flex items-center gap-1">
          <CalendarClock className="size-3" aria-hidden /> {date(rfq.deadline, lang)}
        </span>
        {rfq.suppliers_count != null ? (
          <span className="inline-flex items-center gap-1">
            <Users className="size-3" aria-hidden /> {num(rfq.suppliers_count, lang)}
          </span>
        ) : null}
        {rfq.status === "draft" ? (
          <span className="inline-flex items-center gap-1 text-gold">
            <Send className="size-3" aria-hidden /> {t("rfqs.dispatch")}
          </span>
        ) : null}
        </div>
      </button>
    </article>
  );
}

function NewRfqDrawer({
  open,
  preselectedPr,
  onClose,
  onCreated,
}: {
  open: boolean;
  preselectedPr: string | null;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [prId, setPrId] = useState(preselectedPr ?? "");
  const [title, setTitle] = useState("");
  const [supplierIds, setSupplierIds] = useState<string[]>([]);
  const [deadlineDays, setDeadlineDays] = useState(7);
  const [mode, setMode] = useState<"draft_only" | "semi_auto" | "auto">("draft_only");
  const [error, setError] = useState("");

  const requests = useQuery({
    queryKey: ["requests", "ready"],
    queryFn: () => api.get<Page<PurchaseRequest>>("/api/v1/requests?page_size=100"),
    enabled: open,
  });
  const suppliers = useQuery({
    queryKey: ["suppliers", "picker"],
    queryFn: () => api.get<Page<Supplier>>("/api/v1/suppliers?page_size=100"),
    enabled: open,
  });

  // Only requisitions that passed clarification/submission can be sourced.
  const ready = (requests.data?.items ?? []).filter((row) => row.status === "ready");

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string }>("/api/v1/rfqs", {
        pr_id: prId,
        title: title || undefined,
        supplier_ids: supplierIds,
        deadline_days: deadlineDays,
        mode,
      }),
    onSuccess: (result) => onCreated(result.id),
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("rfqs.newRfq")}
      description={t("rfqs.subtitle")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={supplierIds.length === 0 || !prId || create.isPending}
            onClick={() => {
              setError("");
              create.mutate();
            }}
          >
            {create.isPending ? <Spinner /> : null}
            {t("common.save")}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="label" htmlFor="pr">
            {t("requests.title")}
          </label>
          <select id="pr" className="input" value={prId} onChange={(event) => setPrId(event.target.value)}>
            <option value="">—</option>
            {ready.map((row) => (
              <option key={row.id} value={row.id}>
                {row.code} · {row.title.slice(0, 60)}
              </option>
            ))}
          </select>
          {ready.length === 0 ? <p className="mt-1 text-2xs text-[color:var(--warn-ink)]">{t("requests.submitForRfq")}</p> : null}
        </div>
        <div>
          <label className="label" htmlFor="title">
            {t("rfqs.title")}
          </label>
          <input id="title" className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="deadline">
              {t("common.deadline")}
            </label>
            <input id="deadline" type="number" min={1} max={60} className="input tabular" value={deadlineDays} onChange={(event) => setDeadlineDays(Number(event.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="mode">
              {t("rfqs.fields.mode")}
            </label>
            <select id="mode" className="input" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
              <option value="draft_only">{t("rfqs.modes.draft_only")}</option>
              <option value="semi_auto">{t("rfqs.modes.semi_auto")}</option>
              <option value="auto">{t("rfqs.modes.auto")}</option>
            </select>
          </div>
        </div>
        <div>
          <p className="label">{t("suppliers.title")}</p>
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-line p-2">
            {(suppliers.data?.items ?? []).map((supplier) => {
              const checked = supplierIds.includes(supplier.id);
              return (
                <label key={supplier.id} className="flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1 hover:bg-surface-raised">
                  <span className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setSupplierIds((current) => (checked ? current.filter((id) => id !== supplier.id) : [...current, supplier.id]))
                      }
                    />
                    {supplier.name}
                  </span>
                  <span className="text-2xs text-ink-subtle">{supplier.country} · {num(supplier.overall_score, "en", 1)}</span>
                </label>
              );
            })}
            {(suppliers.data?.items ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </div>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-[color:var(--danger-ink)]">
            {error}
          </p>
        ) : null}
      </div>
    </Drawer>
  );
}
