import { ArrowRight, CheckCircle2, ShieldCheck, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/session";
import { Brand } from "@/components/Brand";
import { Spinner } from "@/components/ui";

export default function LoginPage() {
  const { t } = useTranslation();
  const { login, verifyMfa, status, challenge } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (status === "authenticated") return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(email.trim(), password);
      if (!result.mfa) navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const submitMfa = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await verifyMfa(code.trim());
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel */}
      <section className="relative hidden flex-col justify-between overflow-hidden border-e border-[color:var(--line)] bg-[color:var(--navy-900)] p-12 text-white lg:flex">
        {/* Two soft gold pools + a faint engineering grid: depth without a photograph. */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.16]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 16% 18%, var(--gold) 0, transparent 40%), radial-gradient(circle at 84% 82%, var(--gold) 0, transparent 44%)",
          }}
          aria-hidden
        />
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage:
              "linear-gradient(to right, rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.05) 1px, transparent 1px)",
            backgroundSize: "56px 56px",
            maskImage: "radial-gradient(ellipse at 30% 30%, black 0, transparent 72%)",
            WebkitMaskImage: "radial-gradient(ellipse at 30% 30%, black 0, transparent 72%)",
          }}
          aria-hidden
        />
        <Brand />
        <div className="relative max-w-md">
          <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-gold/40 px-3 py-1 text-2xs uppercase tracking-[0.2em] text-[color:var(--gold-300)]">
            <Sparkles className="size-3" aria-hidden /> {t("app.tagline")}
          </p>
          <h1 className="text-3xl font-semibold leading-snug">{t("auth.title")}</h1>
          <p className="mt-3 text-sm text-[color:var(--ink-muted)]">{t("auth.subtitle")}</p>
          <ul className="mt-8 space-y-3">
            {(t("auth.highlights", { returnObjects: true }) as unknown as string[]).map((item) => (
              <li key={item} className="flex items-start gap-2 text-sm text-white/90">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-gold" aria-hidden />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative flex items-center gap-2 text-2xs text-[color:var(--ink-subtle)]">
          <ShieldCheck className="size-4" aria-hidden />
          {t("auth.trust")}
        </p>
      </section>

      {/* Form panel */}
      <section className="relative flex items-center justify-center bg-surface px-5 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Brand />
          </div>
          <h2 className="text-2xl font-semibold text-ink">{challenge ? t("auth.mfaTitle") : t("auth.title")}</h2>
          <p className="mt-1 text-sm text-ink-muted">{t("auth.subtitle")}</p>

          {challenge ? (
            <form className="mt-6 space-y-4" onSubmit={submitMfa}>
              <div>
                <label className="label" htmlFor="code">
                  {t("auth.mfaCode")}
                </label>
                <input
                  id="code"
                  className="input tabular tracking-[0.3em]"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  required
                />
              </div>
              {error ? (
                <p role="alert" className="text-sm text-[color:var(--danger-ink)]">
                  {error}
                </p>
              ) : null}
              <button className="btn-primary w-full" type="submit" disabled={busy}>
                {busy ? <Spinner /> : null}
                {t("auth.mfaVerify")}
              </button>
            </form>
          ) : (
            <form className="mt-6 space-y-4" onSubmit={submit}>
              <div>
                <label className="label" htmlFor="email">
                  {t("auth.email")}
                </label>
                <input
                  id="email"
                  type="email"
                  className="input"
                  autoComplete="username"
                  dir="ltr"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>
              <div>
                <label className="label" htmlFor="password">
                  {t("auth.password")}
                </label>
                <input
                  id="password"
                  type="password"
                  className="input"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  minLength={8}
                />
              </div>
              {error ? (
                <p role="alert" className="text-sm text-[color:var(--danger-ink)]">
                  {error}
                </p>
              ) : null}
              <button className="btn-primary w-full" type="submit" disabled={busy}>
                {busy ? <Spinner /> : null}
                {busy ? t("auth.signingIn") : t("auth.signIn")}
                <ArrowRight className="size-4 rtl:rotate-180" aria-hidden />
              </button>
            </form>
          )}
          <p className="mt-6 text-2xs leading-relaxed text-ink-subtle">{t("auth.demoHint")}</p>
        </div>
      </section>
    </div>
  );
}
