import { useQuery } from "@tanstack/react-query";
import { Check, Copy, Languages, MessageSquare } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import { Badge, Card, EmptyState, ErrorState, LoadingBlock, Tabs, TabList, TabPanel, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { AppLang } from "@/i18n";
import type { TemplateLibrary } from "@/lib/types";

export default function TemplatesPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [tab, setTab] = useState("whatsapp");
  const [copied, setCopied] = useState<string | null>(null);

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

  const channels = library.data?.channels ?? [];

  return (
    <div className="space-y-4">
      <PageHeading
        eyebrow={t("templates.eyebrow")}
        title={t("templates.title")}
        subtitle={t("templates.subtitle")}
        actions={library.data ? <Badge tone="gold">{t("templates.count", { count: library.data.total })}</Badge> : undefined}
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
