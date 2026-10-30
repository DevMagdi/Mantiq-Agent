import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Plus, Radar, ShieldAlert, Star } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import { SupplierChannelsPanel } from "@/components/SupplierChannelsPanel";
import { Badge, Card, Drawer, EmptyState, ErrorState, LoadingBlock, Progress, Spinner, StatusChip, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, num, percent } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { DiscoveryJob, Page, Supplier } from "@/lib/types";
import type { ColumnDef } from "@tanstack/react-table";

export default function SuppliersPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const queryClient = useQueryClient();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [country, setCountry] = useState("EG");
  const [error, setError] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryJob | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [selected, setSelected] = useState<Supplier | null>(null);

  const list = useQuery({
    queryKey: ["suppliers"],
    queryFn: () => api.get<Page<Supplier>>("/api/v1/suppliers?page_size=200"),
  });

  const runDiscovery = useMutation({
    mutationFn: () => api.post<DiscoveryJob>("/api/v1/suppliers/discovery", { query, country, max_results: 12 }),
    onSuccess: (job) => {
      setDiscovery(job);
      setError("");
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const approve = async (candidateId: string, approve: boolean) => {
    try {
      await api.post(`/api/v1/suppliers/discovery/${approve ? "approve" : "reject"}`, { candidate_ids: [candidateId] });
      await queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      toast.push(t(approve ? "suppliers.approve" : "common.cancel"), "ok");
      if (discovery) {
        setDiscovery({
          ...discovery,
          candidates: discovery.candidates.map((candidate) =>
            candidate.id === candidateId ? { ...candidate, status: approve ? "approved" : "rejected" } : candidate,
          ),
        });
      }
    } catch (err) {
      toast.push(errorMessage(err), "danger");
    }
  };

  const columns: ColumnDef<Supplier, unknown>[] = [
    {
      accessorKey: "name",
      header: t("common.supplier"),
      cell: ({ row }) => (
        <div className="max-w-xs">
          <p className="truncate text-sm text-ink">{lang === "ar" && row.original.name_ar ? row.original.name_ar : row.original.name}</p>
          <p className="text-2xs text-ink-subtle">{row.original.categories.join(" · ")}</p>
        </div>
      ),
    },
    { accessorKey: "country", header: t("suppliers.country"), cell: ({ row }) => <span className="text-xs">{row.original.country} · {row.original.city}</span> },
    { accessorKey: "status", header: t("common.status"), cell: ({ row }) => <StatusChip status={row.original.status} /> },
    {
      accessorKey: "overall_score",
      header: t("suppliers.score"),
      cell: ({ row }) => (
        <div className="flex w-28 items-center gap-2">
          <Progress value={Number(row.original.overall_score)} tone={Number(row.original.overall_score) >= 80 ? "ok" : "warn"} />
          <span className="tabular text-2xs">{num(row.original.overall_score, lang, 1)}</span>
        </div>
      ),
    },
    {
      id: "certifications",
      header: t("suppliers.certifications"),
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {(row.original.certifications ?? []).slice(0, 2).map((cert) => (
            <Badge key={cert.code} tone="info">
              <BadgeCheck className="size-3" aria-hidden /> {cert.code}
            </Badge>
          ))}
          {(row.original.certifications ?? []).length === 0 ? <span className="text-2xs text-ink-subtle">—</span> : null}
        </div>
      ),
    },
    {
      id: "sanctions",
      header: t("suppliers.flags"),
      cell: ({ row }) =>
        row.original.sanctions_status && row.original.sanctions_status !== "clear" ? (
          <Badge tone="danger">
            <ShieldAlert className="size-3" aria-hidden /> {row.original.sanctions_status}
          </Badge>
        ) : (
          <Badge tone="ok">{t("common.no")}</Badge>
        ),
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeading
        title={t("suppliers.title")}
        subtitle={t("suppliers.subtitle")}
        actions={
          <button type="button" className="btn-primary" onClick={() => setAddOpen(true)}>
            <Plus className="size-4" aria-hidden /> {t("suppliers.add")}
          </button>
        }
      />

      <Card className="p-4">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-64 flex-1">
            <label className="label" htmlFor="discovery-query">
              {t("suppliers.discoveryQuery")}
            </label>
            <input id="discovery-query" className="input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="rebar distributor Cairo" />
          </div>
          <div>
            <label className="label" htmlFor="discovery-country">
              {t("suppliers.country")}
            </label>
            <select id="discovery-country" className="input" value={country} onChange={(event) => setCountry(event.target.value)}>
              {["EG", "AE", "SA", "KW", "QA"].map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </div>
          <button type="button" className="btn-secondary" disabled={query.trim().length < 3 || runDiscovery.isPending} onClick={() => runDiscovery.mutate()}>
            {runDiscovery.isPending ? <Spinner /> : <Radar className="size-4" aria-hidden />}
            {t("suppliers.run")}
          </button>
        </div>

        {error ? <p role="alert" className="mt-2 text-sm text-[color:var(--danger-ink)]">{error}</p> : null}
        {discovery ? (
          <div className="mt-3 rounded-md border border-line">
            <div className="flex items-center justify-between border-b border-line px-3 py-2 text-xs text-ink-muted">
              <span>{`${discovery.found} ${t("suppliers.title")}`}</span>
              <Badge tone="info">{discovery.provider} · {t("suppliers.sandbox")}</Badge>
            </div>
            <ul className="divide-y divide-line/60">
              {(discovery.candidates ?? []).map((candidate) => (
                <li key={candidate.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-sm text-ink">{candidate.name}</p>
                    <p className="text-2xs text-ink-subtle">
                      {candidate.country} · {candidate.city ?? "—"} · {percent(candidate.confidence * 100)} · {candidate.dedup_status}
                    </p>
                    {candidate.source_url ? (
                      <a href={candidate.source_url} target="_blank" rel="noreferrer" className="text-2xs text-gold hover:underline" dir="ltr">
                        {candidate.source_url}
                      </a>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusChip status={candidate.status} />
                    {candidate.status === "pending" ? (
                      <>
                        <button type="button" className="btn-secondary text-2xs" onClick={() => void approve(candidate.id, true)}>
                          {t("suppliers.approve")}
                        </button>
                        <button type="button" className="btn-ghost text-2xs" onClick={() => void approve(candidate.id, false)}>
                          {t("common.cancel")}
                        </button>
                      </>
                    ) : null}
                  </div>
                </li>
              ))}
              {(discovery.candidates ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
            </ul>
          </div>
        ) : null}
      </Card>

      <Card className="overflow-hidden">
        {list.isLoading ? (
          <LoadingBlock rows={8} />
        ) : list.error ? (
          <ErrorState message={errorMessage(list.error)} onRetry={() => void list.refetch()} />
        ) : (
          <DataTable data={list.data?.items ?? []} columns={columns} onRowClick={setSelected} empty={<EmptyState title={t("common.empty")} />} searchPlaceholder={t("suppliers.title")} />
        )}
      </Card>

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? (lang === "ar" && selected.name_ar ? selected.name_ar : selected.name) : ""} description={selected ? `${selected.country} · ${selected.city}` : undefined}>
        {selected ? (
          <div className="space-y-4 text-sm">
            <div className="flex items-center gap-3">
              <StatusChip status={selected.status} />
              <Badge tone="gold">
                <Star className="size-3" aria-hidden /> {num(selected.overall_score, lang, 1)}
              </Badge>
              {selected.website ? (
                <a href={selected.website} target="_blank" rel="noreferrer" className="text-2xs text-gold hover:underline" dir="ltr">
                  {selected.website}
                </a>
              ) : null}
            </div>
            <section>
              <p className="label">{t("suppliers.categories")}</p>
              <div className="flex flex-wrap gap-1">
                {selected.categories.map((category) => (
                  <span key={category} className="chip">{category}</span>
                ))}
              </div>
            </section>
            <section>
              <p className="label">{t("suppliers.score")}</p>
              {selected.ratings ? (
                <div className="space-y-1.5">
                  {Object.entries(selected.ratings).map(([key, value]) => (
                    <div key={key} className="flex items-center gap-2">
                      <span className="w-28 text-2xs text-ink-muted">{key.replaceAll("_", " ")}</span>
                      <Progress value={Number(value)} tone="info" />
                      <span className="tabular text-2xs">{num(value, lang, 1)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-ink-muted">{t("common.none")}</p>
              )}
            </section>
            <section>
              <p className="label">{t("suppliers.certifications")}</p>
              {(selected.certifications ?? []).length > 0 ? (
                <ul className="space-y-1">
                  {selected.certifications?.map((cert) => (
                    <li key={cert.code} className="flex items-center justify-between rounded border border-line px-2 py-1 text-xs">
                      <span>{cert.code}</span>
                      <span className="text-ink-subtle">{date(cert.expires_at, lang)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-ink-muted">{t("common.none")}</p>
              )}
            </section>
            <section>
              <p className="label">{t("channels.supplierTitle")}</p>
              <SupplierChannelsPanel supplierId={selected.id} />
            </section>
            {(selected.flags ?? []).length > 0 ? (
              <section>
                <p className="label">{t("suppliers.flags")}</p>
                <ul className="space-y-1 text-2xs text-[color:var(--warn-ink)]">
                  {selected.flags?.map((flag) => (
                    <li key={`${flag.type}-${flag.at}`}>{flag.type}: {flag.reason}</li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        ) : null}
      </Drawer>

      <AddSupplierDrawer open={addOpen} onClose={() => setAddOpen(false)} onCreated={async () => {
        setAddOpen(false);
        await queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      }} />
    </div>
  );
}

function AddSupplierDrawer({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => Promise<void> }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [name, setName] = useState("");
  const [nameAr, setNameAr] = useState("");
  const [country, setCountry] = useState("EG");
  const [city, setCity] = useState("");
  const [email, setEmail] = useState("");
  const [categories, setCategories] = useState("STEEL");

  const create = useMutation({
    mutationFn: () =>
      api.post("/api/v1/suppliers", {
        name: name.trim(),
        name_ar: nameAr.trim(),
        country,
        city,
        email: email || undefined,
        categories: categories.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean),
      }),
    onSuccess: async () => {
      toast.push(t("suppliers.add"), "ok");
      setName("");
      setNameAr("");
      setEmail("");
      await onCreated();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("suppliers.add")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="submit" form="add-supplier" className="btn-primary" disabled={name.trim().length < 2 || create.isPending}>
            {create.isPending ? <Spinner /> : null}
            {t("common.save")}
          </button>
        </div>
      }
    >
      <form id="add-supplier" className="space-y-3" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="supplier-name">Name (EN)</label>
          <input id="supplier-name" className="input" value={name} onChange={(event) => setName(event.target.value)} required />
        </div>
        <div>
          <label className="label" htmlFor="supplier-name-ar">الاسم (AR)</label>
          <input id="supplier-name-ar" className="input" value={nameAr} onChange={(event) => setNameAr(event.target.value)} dir="rtl" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="supplier-country">{t("suppliers.country")}</label>
            <select id="supplier-country" className="input" value={country} onChange={(event) => setCountry(event.target.value)}>
              {["EG", "AE", "SA", "KW", "QA"].map((code) => (
                <option key={code} value={code}>{code}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="supplier-city">City</label>
            <input id="supplier-city" className="input" value={city} onChange={(event) => setCity(event.target.value)} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="supplier-email">{t("auth.email")}</label>
          <input id="supplier-email" type="email" className="input" dir="ltr" value={email} onChange={(event) => setEmail(event.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="supplier-categories">{t("suppliers.categories")}</label>
          <input id="supplier-categories" className="input" value={categories} onChange={(event) => setCategories(event.target.value)} placeholder="STEEL, PACKAGING" />
        </div>
      </form>
    </Drawer>
  );
}
