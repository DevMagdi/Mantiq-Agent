import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Languages, MessageSquare, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  Drawer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Tabs,
  TabList,
  TabPanel,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { TemplateLibrary } from "@/lib/types";

export default function TemplatesPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [tab, setTab] = useState("whatsapp");
  const [copied, setCopied] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState({
    code: "",
    channel: "whatsapp",
    locale: "ar",
    name: "",
    subject: "",
    body: "",
  });

  const library = useQuery({
    queryKey: ["template-library"],
    queryFn: () => api.get<TemplateLibrary>("/api/v1/templates/library"),
  });

  const copy = async (id: string, body: string) => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(id);
      toast.push(t("templates.copied"), "ok");
    } catch {
      toast.push(t("templates.copyFailed"), "danger");
    }
  };

  const save = useMutation({
    mutationFn: () => api.post<{ id: string; version: number }>("/api/v1/templates", draft),
    onSuccess: (result) => {
      toast.push(t("templates.saved", { version: result.version }), "ok");
      setEditorOpen(false);
      setDraft({ code: "", channel: "whatsapp", locale: "ar", name: "", subject: "", body: "" });
      void queryClient.invalidateQueries({ queryKey: ["template-library"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const channels = library.data?.channels ?? [];
  const mayEdit = can("admin:templates");

  return (
    <div className="space-y-4">
      <PageHeading
        eyebrow={t("templates.eyebrow")}
        title={t("templates.title")}
        subtitle={t("templates.subtitle")}
        actions={
          <div className="flex items-center gap-2">
            {library.data ? <Badge tone="gold">{t("templates.count", { count: library.data.total })}</Badge> : null}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setEditorOpen(true)}
              disabled={!mayEdit}
              title={mayEdit ? t("templates.newVersion") : t("templates.needsPermission")}
            >
              <Plus className="size-4" aria-hidden />
              {t("templates.newVersion")}
            </button>
          </div>
        }
      />

      {library.isLoading ? (
        <Card>
          <LoadingBlock rows={8} />
        </Card>
      ) : library.error ? (
        <Card>
          <ErrorState message={errorMessage(library.error)} onRetry={() => void library.refetch()} />
        </Card>
      ) : channels.length === 0 ? (
        <Card>
          <EmptyState icon={<MessageSquare className="size-5" aria-hidden />} title={t("templates.empty")} />
        </Card>
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabList items={channels.map((row) => ({ value: row.channel, label: `${t(`channels.names.${row.channel}`, row.channel)} (${row.count})` }))} />
          {channels.map((row) => (
            <TabPanel key={row.channel} value={row.channel}>
              <div className="grid gap-3 md:grid-cols-2">
                {(library.data?.items[row.channel] ?? []).map((template) => (
                  <Card key={template.id} className="flex flex-col p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium text-ink">{lm(template.name || template.code, template.code, lang, t)}</p>
                      <div className="flex items-center gap-1">
                        <Badge tone="info">
                          <Languages className="size-3" aria-hidden /> {template.locale.toUpperCase()}
                        </Badge>
                        <Badge tone="neutral">{template.kind}</Badge>
                      </div>
                    </div>
                    {template.subject ? <p className="mt-1 text-2xs text-ink-subtle">{template.subject}</p> : null}
                    <pre
                      className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-base/60 p-2 text-xs text-ink-muted"
                      dir={template.locale === "ar" ? "rtl" : "ltr"}
                    >
                      {template.body}
                    </pre>
                    <div className="mt-2 flex flex-wrap items-center gap-1">
                      {(template.variables ?? []).map((variable) => (
                        <span key={variable} className="chip border-line text-2xs text-ink-subtle">{`{{${variable}}}`}</span>
                      ))}
                    </div>
                    <div className="mt-3 flex items-center justify-between">
                      <span className="text-2xs text-ink-subtle">{`v${template.version}`}</span>
                      <button type="button" className="btn-ghost text-2xs" onClick={() => void copy(template.id, template.body)}>
                        {copied === template.id ? <Check className="size-3" aria-hidden /> : <Copy className="size-3" aria-hidden />}
                        {copied === template.id ? t("templates.copied") : t("templates.copy")}
                      </button>
                    </div>
                  </Card>
                ))}
              </div>
            </TabPanel>
          ))}
        </Tabs>
      )}

      <Drawer
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        title={t("templates.editorTitle")}
        description={t("templates.editorBody")}
        footer={
          <div className="flex items-center justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setEditorOpen(false)}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={save.isPending || !draft.code || !draft.name || !draft.body}
              onClick={() => save.mutate()}
            >
              {save.isPending ? t("common.loading") : t("common.save")}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="label">{t("templates.code")}</span>
              <input
                className="input"
                dir="ltr"
                value={draft.code}
                onChange={(event) => setDraft({ ...draft, code: event.target.value })}
                placeholder="reminder"
              />
            </label>
            <label className="block text-sm">
              <span className="label">{t("templates.name")}</span>
              <input
                className="input"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </label>
            <label className="block text-sm">
              <span className="label">{t("templates.channel")}</span>
              <select
                className="input"
                value={draft.channel}
                onChange={(event) => setDraft({ ...draft, channel: event.target.value })}
              >
                <option value="whatsapp">WhatsApp</option>
                <option value="email">{t("channels.names.email", "Email")}</option>
                <option value="voice">{t("channels.names.voice", "Voice")}</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="label">{t("templates.locale")}</span>
              <select
                className="input"
                value={draft.locale}
                onChange={(event) => setDraft({ ...draft, locale: event.target.value })}
              >
                <option value="ar">العربية</option>
                <option value="en">English</option>
              </select>
            </label>
          </div>
          <label className="block text-sm">
            <span className="label">{t("templates.subject")}</span>
            <input
              className="input"
              value={draft.subject}
              onChange={(event) => setDraft({ ...draft, subject: event.target.value })}
            />
          </label>
          <div className="block text-sm">
            <label className="label" htmlFor="template-body">
              {t("templates.body")}
            </label>
            <textarea
              id="template-body"
              className="input min-h-40"
              dir={draft.locale === "ar" ? "rtl" : "ltr"}
              value={draft.body}
              onChange={(event) => setDraft({ ...draft, body: event.target.value })}
            />
            <span className="mt-1 block text-2xs text-ink-subtle">{t("templates.variablesHint")}</span>
          </div>
        </div>
      </Drawer>
    </div>
  );
}

/** Template names are stored per row; show the localised name when the row carries the marker. */
function lm(name: string, code: string, lang: AppLang, t: (key: string) => string): string {
  if (lang !== "ar") return name || code;
  const key = `templates.names.${code}`;
  const translated = t(key);
  return translated === key ? name || code : translated;
}
