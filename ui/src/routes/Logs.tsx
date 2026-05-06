import { useDeferredValue, useEffect, useState } from "react";

import { buildLogsUrl } from "../lib/api";
import { Input, Panel, SectionBlurb, StatusBadge } from "../components/shell";
import { useAppStore } from "../store/appStore";

export default function Logs() {
  const { logs, fetchLogs, appendLog } = useAppStore();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());

  useEffect(() => {
    void fetchLogs();
    const stream = new EventSource(buildLogsUrl());
    stream.onmessage = (event) => {
      appendLog(JSON.parse(event.data));
    };
    return () => stream.close();
  }, [appendLog, fetchLogs]);

  const filtered = logs.filter((entry) => {
    if (!deferredQuery) {
      return true;
    }
    return `${entry.kind} ${entry.message}`.toLowerCase().includes(deferredQuery);
  });

  return (
    <div className="space-y-6">
      <Panel
        title="Observability Search"
        eyebrow="Logs"
        aside="Filter the event stream when you need to answer a precise operational question instead of scrolling raw output."
      >
        <div className="grid gap-4 lg:grid-cols-[1fr_auto_auto] lg:items-end">
          <label className="text-sm font-semibold text-ink">
            Search logs
            <Input placeholder="rss.fail, doctor, write.dispatch..." value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">Loaded events</p>
            <p className="mt-3 text-2xl font-semibold tracking-tight text-ink">{logs.length}</p>
          </div>
          <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">Filtered view</p>
            <p className="mt-3 text-2xl font-semibold tracking-tight text-ink">{filtered.length}</p>
          </div>
        </div>
      </Panel>

      <Panel title="Event Tape" eyebrow="Runtime" aside="The stream below merges history with live SSE events from the control-plane backend.">
        <div className="rounded-[28px] bg-ink px-4 py-4 text-white shadow-panel">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
            <SectionBlurb>
              <span className="text-white/78">Search term:</span>{" "}
              <span className="font-mono text-white">{deferredQuery || "none"}</span>
            </SectionBlurb>
            <StatusBadge tone="live">sse attached</StatusBadge>
          </div>
          <div className="max-h-[72vh] space-y-2 overflow-auto font-mono text-xs leading-6">
            {filtered.length ? (
              filtered.map((entry, index) => (
                <div key={`${entry.kind}-${index}`} className="rounded-[18px] border border-white/8 bg-white/[0.035] px-3 py-2">
                  <span className="text-emerald-300">{entry.kind}</span>{" "}
                  <span className="text-white/86">{entry.message}</span>
                </div>
              ))
            ) : (
              <p className="text-white/70">No log lines match the current filter.</p>
            )}
          </div>
        </div>
      </Panel>
    </div>
  );
}
