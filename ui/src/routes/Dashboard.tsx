import { useEffect, useState } from "react";

import { api, buildLogsUrl } from "../lib/api";
import { useAppStore } from "../store/appStore";
import type { RunMode } from "../types";
import { ActionButton, Panel, SectionBlurb, StatusBadge, SummaryChip } from "../components/shell";

const quickModes: Array<{ label: string; mode: RunMode; blurb: string }> = [
  { label: "Run Digest", mode: "digest", blurb: "Full curated output to the configured vault." },
  { label: "Run Raw", mode: "raw", blurb: "Capture evidence only without the final digest layer." },
  { label: "Dry Run", mode: "dry-run", blurb: "Preview write targets and orchestration without vault I/O." },
  { label: "Test Mode", mode: "test", blurb: "Exercise the pipeline with non-writing execution mode." }
];

export default function Dashboard() {
  const { status, fetchStatus, logs, fetchLogs, appendLog } = useAppStore();
  const [submitting, setSubmitting] = useState<RunMode | null>(null);

  useEffect(() => {
    void fetchStatus();
    void fetchLogs();
    const stream = new EventSource(buildLogsUrl());
    stream.onmessage = (event) => {
      appendLog(JSON.parse(event.data));
    };
    return () => stream.close();
  }, [appendLog, fetchLogs, fetchStatus]);

  useEffect(() => {
    if (!status?.run.active) {
      return;
    }
    const timer = window.setInterval(() => {
      void fetchStatus();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [fetchStatus, status?.run.active]);

  async function handleRun(mode: RunMode) {
    setSubmitting(mode);
    try {
      await api.runWorkflow(mode);
      await Promise.all([fetchStatus(), fetchLogs()]);
    } finally {
      setSubmitting(null);
    }
  }

  const warningSources = (status?.feed_health.sources ?? []).filter((entry) => entry.status !== "ok").slice(0, 5);

  return (
    <div className="space-y-6">
      <Panel
        title="Operational Snapshot"
        eyebrow="Status"
        aside="The dashboard should answer three questions immediately: is the pipeline healthy, where will output land, and what is worth checking before the next run."
      >
        <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-[28px] border border-black/5 bg-parchment/90 p-5 shadow-inset">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-slate">Run Status</p>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <h4 className="font-display text-4xl leading-none tracking-tight text-ink">
                    {status?.run.active ? "Digest In Flight" : "Ready To Run"}
                  </h4>
                  <StatusBadge tone={status?.run.active ? "live" : "idle"}>
                    {status?.run.active ? status.run.mode : "idle"}
                  </StatusBadge>
                </div>
                <p className="mt-4 max-w-2xl text-sm leading-7 text-slate">
                  Target vault: {status?.config_summary.vault_path || "Awaiting configuration"}
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <SummaryChip label="Last Return" value={status?.run.return_code ?? "-"} />
                <SummaryChip label="Events Seen" value={status?.run.event_count ?? 0} />
              </div>
            </div>

            <div className="mt-6 grid gap-3 md:grid-cols-4">
              <MetricCard label="RSS Sources" value={status?.config_summary.rss_count ?? 0} />
              <MetricCard label="YouTube Channels" value={status?.config_summary.youtube_count ?? 0} />
              <MetricCard label="Warnings" value={status?.feed_health.counts.warning ?? 0} />
              <MetricCard label="Errors" value={status?.feed_health.counts.error ?? 0} />
            </div>
          </div>

          <div className="rounded-[28px] border border-black/5 bg-white/88 p-5">
            <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-ember">Quick Context</p>
            <div className="mt-4 space-y-4 text-sm text-slate">
              <div>
                <p className="font-semibold text-ink">Last health snapshot</p>
                <p className="mt-1">Run date: {status?.feed_health.run_date || "N/A"}</p>
              </div>
              <div>
                <p className="font-semibold text-ink">Recent run window</p>
                <p className="mt-1">Started: {status?.run.started_at || "N/A"}</p>
                <p className="mt-1">Finished: {status?.run.finished_at || "N/A"}</p>
              </div>
              <div>
                <p className="font-semibold text-ink">Operator note</p>
                <p className="mt-1 leading-6">
                  Use `Dry Run` before source or vault changes; use `Run Raw` when the evidence layer needs inspection before curation.
                </p>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel title="Quick Run" eyebrow="Execution" aside="Four entry points, each with a distinct operational purpose.">
          <div className="grid gap-4 lg:grid-cols-2">
            {quickModes.map((entry) => (
              <button
                key={entry.mode}
                className="rounded-[28px] border border-black/5 bg-white/90 p-5 text-left transition hover:-translate-y-0.5 hover:border-black/10 hover:shadow-panel disabled:cursor-not-allowed disabled:opacity-50"
                disabled={Boolean(submitting)}
                onClick={() => void handleRun(entry.mode)}
                type="button"
              >
                <div className="flex items-center justify-between gap-4">
                  <p className="font-display text-2xl tracking-tight text-ink">{entry.label}</p>
                  <StatusBadge tone={submitting === entry.mode ? "live" : "idle"}>
                    {submitting === entry.mode ? "running" : entry.mode}
                  </StatusBadge>
                </div>
                <p className="mt-3 text-sm leading-6 text-slate">{entry.blurb}</p>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Feed Health Watchlist" eyebrow="Signals" aside="Top non-OK sources from the latest run.">
          <div className="space-y-3">
            {warningSources.length ? (
              warningSources.map((entry) => (
                <div key={`${entry.source}-${entry.detail}`} className="rounded-[24px] border border-amber-200/60 bg-amber-50/70 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="font-semibold text-ink">{entry.source}</p>
                    <StatusBadge tone="warn">{entry.status}</StatusBadge>
                  </div>
                  <p className="mt-2 text-sm text-slate">items={entry.item_count}</p>
                  <p className="mt-2 text-sm leading-6 text-slate">{entry.detail || "No detail provided."}</p>
                </div>
              ))
            ) : (
              <SectionBlurb>No warning sources in the latest feed-health snapshot.</SectionBlurb>
            )}
          </div>
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Panel title="Recent Outputs" eyebrow="Vault" aside="Use this list to confirm that the latest artifact landed in the expected folder.">
          <div className="space-y-3">
            {status?.recent_outputs.length ? (
              status.recent_outputs.map((item) => (
                <div key={item.path} className="rounded-[24px] border border-black/5 bg-white/92 px-4 py-4">
                  <p className="font-semibold text-ink">{item.name}</p>
                  <p className="mt-2 break-all font-mono text-[11px] leading-5 text-slate">{item.path}</p>
                  <p className="mt-2 text-xs uppercase tracking-[0.24em] text-slate/80">{item.updated_at}</p>
                </div>
              ))
            ) : (
              <SectionBlurb>No generated files detected yet.</SectionBlurb>
            )}
          </div>
        </Panel>

        <Panel title="Live Log Stream" eyebrow="Observability" aside="Read the final twenty events exactly as they arrived from the backend SSE stream.">
          <div className="rounded-[28px] bg-ink px-4 py-4 text-white shadow-panel">
            <div className="mb-3 flex items-center justify-between gap-3 border-b border-white/10 pb-3">
              <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/65">runtime tail</p>
              <StatusBadge tone={status?.run.active ? "live" : "idle"}>
                {status?.run.active ? "streaming" : "standby"}
              </StatusBadge>
            </div>
            <div className="max-h-[460px] space-y-2 overflow-auto font-mono text-xs leading-6">
              {logs.length ? (
                logs.slice(-20).map((entry, index) => (
                  <div key={`${entry.kind}-${index}`} className="rounded-[18px] border border-white/8 bg-white/[0.03] px-3 py-2">
                    <span className="text-emerald-300">{entry.kind}</span>{" "}
                    <span className="text-white/86">{entry.message}</span>
                  </div>
                ))
              ) : (
                <p className="text-white/70">No log events loaded yet.</p>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/90 px-4 py-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-slate">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-tight text-ink">{value}</p>
    </div>
  );
}
