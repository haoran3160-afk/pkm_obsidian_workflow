import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  PropsWithChildren,
  SelectHTMLAttributes,
  TextareaHTMLAttributes
} from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAppStore } from "../store/appStore";

const navItems = [
  {
    label: "Dashboard",
    to: "/",
    eyebrow: "Ops",
    description: "Run digest jobs and inspect live signals."
  },
  {
    label: "Sources",
    to: "/sources",
    eyebrow: "Registry",
    description: "Curate feeds and channel coverage."
  },
  {
    label: "Output",
    to: "/output",
    eyebrow: "Delivery",
    description: "Route output, vaults, and copy settings."
  },
  {
    label: "Logs",
    to: "/logs",
    eyebrow: "Trace",
    description: "Tail runtime history and incident clues."
  },
  {
    label: "Settings",
    to: "/settings",
    eyebrow: "Runtime",
    description: "Doctor checks and environment posture."
  }
] as const;

const routeTitles: Record<string, { title: string; subtitle: string }> = {
  "/": {
    title: "Digest Command Deck",
    subtitle: "Monitor the pipeline, trigger runs, and verify the last vault-facing output."
  },
  "/sources": {
    title: "Source Curation Desk",
    subtitle: "Shape the signal mix before it reaches the daily digest."
  },
  "/output": {
    title: "Delivery Controls",
    subtitle: "Tune vault routing, write mode, and the final copy layer."
  },
  "/logs": {
    title: "Observability Tape",
    subtitle: "Read the exact run history instead of guessing from a failed screen."
  },
  "/settings": {
    title: "Runtime Diagnostics",
    subtitle: "Check environment posture and run doctor without leaving the console."
  }
};

export function AppShell({ children }: PropsWithChildren) {
  const location = useLocation();
  const { loading, error, clearError, status } = useAppStore();
  const activeRoute = routeTitles[location.pathname] ?? routeTitles["/"];
  const activeRun = status?.run.active ? status.run.mode : "Idle";

  return (
    <div className="min-h-screen bg-noise text-ink">
      <div className="mx-auto flex min-h-screen max-w-[1580px] gap-6 px-4 py-4 lg:px-8 lg:py-6">
        <aside className="hidden w-[320px] shrink-0 rounded-[34px] border border-black/5 bg-[rgba(255,253,250,0.78)] p-6 shadow-panel backdrop-blur xl:block">
          <p className="font-mono text-[11px] uppercase tracking-[0.42em] text-slate">PKM Workflow</p>
          <h1 className="mt-4 font-display text-5xl leading-[0.92] tracking-tight text-ink">
            Control
            <br />
            Room
          </h1>
          <p className="mt-4 max-w-[18rem] text-sm leading-6 text-slate">
            Local-first editorial console for digest generation, source maintenance, and runtime checks.
          </p>

          <div className="mt-8 rounded-[28px] border border-black/5 bg-parchment/80 p-4 shadow-inset">
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-slate">Current Run</p>
                <p className="mt-2 text-2xl font-semibold tracking-tight text-ink">{activeRun}</p>
              </div>
              <StatusBadge tone={status?.run.active ? "live" : "idle"}>
                {status?.run.active ? "LIVE" : "STANDBY"}
              </StatusBadge>
            </div>
            <p className="mt-4 text-sm leading-6 text-slate">
              Vault: {status?.config_summary.vault_path || "Awaiting status"}
            </p>
          </div>

          <nav className="mt-8 space-y-3">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  [
                    "block rounded-[26px] border p-4 transition",
                    isActive
                      ? "border-black/5 bg-ink text-white shadow-panel"
                      : "border-black/5 bg-white/55 text-ink hover:-translate-y-0.5 hover:bg-white/85"
                  ].join(" ")
                }
              >
                {({ isActive }) => (
                  <>
                    <p className={`font-mono text-[10px] uppercase tracking-[0.34em] ${isActive ? "text-white/70" : "text-slate"}`}>
                      {item.eyebrow}
                    </p>
                    <div className="mt-2 flex items-center justify-between gap-4">
                      <p className="text-base font-semibold tracking-tight">{item.label}</p>
                      <span className={`h-2.5 w-2.5 rounded-full ${isActive ? "bg-emerald-300" : "bg-fog"}`} />
                    </div>
                    <p className={`mt-2 text-sm leading-6 ${isActive ? "text-white/72" : "text-slate"}`}>{item.description}</p>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 flex-1">
          <div className="rounded-[34px] border border-black/5 bg-[rgba(255,253,250,0.74)] p-4 shadow-panel backdrop-blur md:p-6">
            <div className="border-b border-black/5 pb-5">
              <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
                <div className="max-w-3xl">
                  <p className="font-mono text-[11px] uppercase tracking-[0.4em] text-ember">Editorial Console</p>
                  <h2 className="mt-3 font-display text-4xl leading-none tracking-tight text-ink md:text-5xl">
                    {activeRoute.title}
                  </h2>
                  <p className="mt-4 max-w-2xl text-sm leading-7 text-slate md:text-[15px]">
                    {activeRoute.subtitle}
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-3">
                  <SummaryChip label="RSS" value={status?.config_summary.rss_count ?? 0} />
                  <SummaryChip label="YouTube" value={status?.config_summary.youtube_count ?? 0} />
                  <SummaryChip label="Write Mode" value={status?.config_summary.write_mode ?? "-"} />
                </div>
              </div>

              <div className="mt-5 flex flex-wrap gap-2 xl:hidden">
                {navItems.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      [
                        "rounded-full border px-4 py-2 text-sm font-semibold transition",
                        isActive ? "border-ink bg-ink text-white" : "border-black/10 bg-white/80 text-ink"
                      ].join(" ")
                    }
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </div>

            {loading ? (
              <div className="mt-5 rounded-[24px] border border-ember/20 bg-ember/8 px-4 py-3 text-sm text-ink">
                Refreshing control-plane data...
              </div>
            ) : null}
            {error ? (
              <div className="mt-5 flex items-start justify-between gap-4 rounded-[24px] border border-red-300/50 bg-red-50 px-4 py-3 text-sm text-red-900">
                <span>{error}</span>
                <button className="font-semibold text-red-900" onClick={clearError} type="button">
                  Dismiss
                </button>
              </div>
            ) : null}

            <div className="mt-6">{children}</div>
          </div>
        </main>
      </div>
    </div>
  );
}

export function Panel({
  title,
  eyebrow,
  children,
  aside
}: PropsWithChildren<{ title: string; eyebrow?: string; aside?: string }>) {
  return (
    <section className="rounded-[30px] border border-black/5 bg-white/76 p-5 shadow-panel backdrop-blur">
      <div className="flex flex-col gap-3 border-b border-black/5 pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          {eyebrow ? <p className="font-mono text-[11px] uppercase tracking-[0.32em] text-ember">{eyebrow}</p> : null}
          <h3 className="mt-2 font-display text-[30px] leading-none tracking-tight text-ink">{title}</h3>
        </div>
        {aside ? <p className="max-w-xl text-sm leading-6 text-slate">{aside}</p> : null}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

export function ActionButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className = "", ...rest } = props;
  return (
    <button
      className={`inline-flex items-center justify-center rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:bg-pine disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...rest}
    />
  );
}

export function GhostButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className = "", ...rest } = props;
  return (
    <button
      className={`inline-flex items-center justify-center rounded-full border border-black/10 bg-white px-4 py-2.5 text-sm font-semibold text-ink transition hover:-translate-y-0.5 hover:border-black/20 ${className}`}
      {...rest}
    />
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return (
    <input
      className={`mt-2 w-full rounded-[20px] border border-black/10 bg-white/90 px-4 py-3 text-sm text-ink outline-none ring-0 transition placeholder:text-slate/70 focus:border-ember ${className}`}
      {...rest}
    />
  );
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return (
    <textarea
      className={`mt-2 w-full rounded-[24px] border border-black/10 bg-white/90 px-4 py-3 text-sm text-ink outline-none transition placeholder:text-slate/70 focus:border-ember ${className}`}
      {...rest}
    />
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", ...rest } = props;
  return (
    <select
      className={`mt-2 w-full rounded-[20px] border border-black/10 bg-white/90 px-4 py-3 text-sm text-ink outline-none transition focus:border-ember ${className}`}
      {...rest}
    />
  );
}

export function SummaryChip({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-3 shadow-inset">
      <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-slate">{label}</p>
      <p className="mt-2 text-xl font-semibold tracking-tight text-ink">{value}</p>
    </div>
  );
}

export function StatusBadge({
  children,
  tone = "idle"
}: PropsWithChildren<{ tone?: "idle" | "live" | "warn" }>) {
  const toneClass =
    tone === "live"
      ? "bg-emerald-100 text-emerald-800"
      : tone === "warn"
        ? "bg-amber-100 text-amber-900"
        : "bg-white/90 text-slate";
  return <span className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.26em] ${toneClass}`}>{children}</span>;
}

export function SectionBlurb({ children }: PropsWithChildren) {
  return <p className="text-sm leading-6 text-slate">{children}</p>;
}
