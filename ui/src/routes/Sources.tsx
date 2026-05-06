import { useDeferredValue, useEffect, useState } from "react";

import { api } from "../lib/api";
import { ActionButton, GhostButton, Input, Panel, SectionBlurb, StatusBadge } from "../components/shell";
import { useAppStore } from "../store/appStore";
import type { FeedSource, SourcesResponse } from "../types";

type SourceKind = "rss_feeds" | "youtube_channels";
type SourceScope = "all" | "enabled" | "disabled";

export default function Sources() {
  const { sources, fetchSources } = useAppStore();
  const [draft, setDraft] = useState<SourcesResponse | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<SourceScope>("all");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());

  useEffect(() => {
    void fetchSources();
  }, [fetchSources]);

  useEffect(() => {
    if (sources) {
      setDraft(sources);
      setSaveState("idle");
    }
  }, [sources]);

  const dirty = Boolean(draft && sources && JSON.stringify(draft) !== JSON.stringify(sources));

  async function save() {
    if (!draft) {
      return;
    }
    setSaveState("saving");
    await api.updateSources(draft);
    await fetchSources();
    setSaveState("saved");
  }

  function updateFeed(kind: SourceKind, index: number, next: FeedSource) {
    setDraft((current) =>
      current
        ? {
            ...current,
            [kind]: current[kind].map((item, itemIndex) => (itemIndex === index ? next : item))
          }
        : current
    );
    setSaveState("idle");
  }

  function removeFeed(kind: SourceKind, index: number) {
    setDraft((current) =>
      current
        ? {
            ...current,
            [kind]: current[kind].filter((_, itemIndex) => itemIndex !== index)
          }
        : current
    );
    setSaveState("idle");
  }

  function addFeed(kind: SourceKind) {
    const entry: FeedSource =
      kind === "rss_feeds"
        ? {
            name: "New RSS Source",
            url: "https://example.com/feed.xml",
            note_folder: "30-Daily/AI-News",
            enabled: true,
            content_type: "news"
          }
        : {
            name: "New YouTube Channel",
            channel_id: "UCxxxxxxxxxxxxxxxxxxxxxx",
            note_folder: "20-Sources/Videos",
            enabled: true
          };
    setDraft((current) => (current ? { ...current, [kind]: [...current[kind], entry] } : current));
    setSaveState("idle");
  }

  const rss = draft?.rss_feeds ?? [];
  const yt = draft?.youtube_channels ?? [];
  const counts = {
    rss: rss.length,
    rssEnabled: rss.filter((item) => item.enabled).length,
    yt: yt.length,
    ytEnabled: yt.filter((item) => item.enabled).length
  };

  return (
    <div className="space-y-6">
      <Panel
        title="Registry Controls"
        eyebrow="Sources"
        aside="Keep the signal mix small, legible, and intentional. This page is for shaping curation inputs, not for dumping every possible feed."
      >
        <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard label="RSS feeds" value={counts.rss} detail={`${counts.rssEnabled} enabled`} />
            <SummaryCard label="YouTube channels" value={counts.yt} detail={`${counts.ytEnabled} enabled`} />
            <SummaryCard label="Unsaved edits" value={dirty ? "Yes" : "No"} detail="Draft diff vs loaded config" />
            <SummaryCard label="Search scope" value={scope} detail={deferredQuery || "all sources"} />
          </div>

          <div className="rounded-[28px] border border-black/5 bg-parchment/90 p-5 shadow-inset">
            <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-slate">Edit Filters</p>
            <div className="mt-4 space-y-4">
              <label className="block text-sm font-semibold text-ink">
                Search by name / URL / channel ID
                <Input placeholder="OpenAI, arXiv, channel id..." value={query} onChange={(event) => setQuery(event.target.value)} />
              </label>
              <div className="flex flex-wrap gap-2">
                {(["all", "enabled", "disabled"] as const).map((item) => (
                  <button
                    key={item}
                    className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                      scope === item ? "bg-ink text-white" : "border border-black/10 bg-white text-ink"
                    }`}
                    onClick={() => setScope(item)}
                    type="button"
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap gap-3">
                <ActionButton onClick={() => addFeed("rss_feeds")}>Add RSS Source</ActionButton>
                <GhostButton onClick={() => addFeed("youtube_channels")}>Add YouTube Channel</GhostButton>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <div className="sticky top-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[24px] border border-black/5 bg-white/90 px-4 py-3 shadow-panel backdrop-blur">
        <div className="flex items-center gap-3">
          <StatusBadge tone={dirty ? "warn" : saveState === "saved" ? "live" : "idle"}>
            {dirty ? "pending changes" : saveState === "saved" ? "saved" : "synced"}
          </StatusBadge>
          <SectionBlurb>
            Save writes the full source registry back to `pkm_config.json` after validation.
          </SectionBlurb>
        </div>
        <ActionButton disabled={!draft || !dirty || saveState === "saving"} onClick={() => void save()}>
          {saveState === "saving" ? "Saving..." : "Save Sources"}
        </ActionButton>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <SourceColumn
          title="RSS / Blogs"
          kind="rss_feeds"
          items={draft?.rss_feeds ?? []}
          deferredQuery={deferredQuery}
          scope={scope}
          onChange={updateFeed}
          onRemove={removeFeed}
        />
        <SourceColumn
          title="YouTube"
          kind="youtube_channels"
          items={draft?.youtube_channels ?? []}
          deferredQuery={deferredQuery}
          scope={scope}
          onChange={updateFeed}
          onRemove={removeFeed}
        />
      </div>
    </div>
  );
}

function SourceColumn({
  title,
  kind,
  items,
  deferredQuery,
  scope,
  onChange,
  onRemove
}: {
  title: string;
  kind: SourceKind;
  items: FeedSource[];
  deferredQuery: string;
  scope: SourceScope;
  onChange: (kind: SourceKind, index: number, next: FeedSource) => void;
  onRemove: (kind: SourceKind, index: number) => void;
}) {
  const filtered = items.filter((item) => {
    const matchesScope = scope === "all" ? true : scope === "enabled" ? item.enabled : !item.enabled;
    const haystack = [item.name, item.url, item.channel_id, item.note_folder].join(" ").toLowerCase();
    const matchesQuery = deferredQuery ? haystack.includes(deferredQuery) : true;
    return matchesScope && matchesQuery;
  });

  return (
    <Panel
      title={title}
      eyebrow={kind === "rss_feeds" ? "Editorial" : "Channel"}
      aside={`${filtered.length} shown out of ${items.length} configured entries.`}
    >
      <div className="space-y-4">
        {filtered.length ? (
          filtered.map((item) => {
            const originalIndex = items.indexOf(item);
            return (
              <div key={`${title}-${item.name}-${originalIndex}`} className="rounded-[28px] border border-black/5 bg-white/90 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-slate">
                      {kind === "rss_feeds" ? "rss source" : "youtube source"}
                    </p>
                    <p className="mt-2 text-lg font-semibold tracking-tight text-ink">{item.name}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge tone={item.enabled ? "live" : "idle"}>{item.enabled ? "enabled" : "disabled"}</StatusBadge>
                    <GhostButton className="px-3 py-2" onClick={() => onRemove(kind, originalIndex)}>
                      Remove
                    </GhostButton>
                  </div>
                </div>

                <div className="mt-4 grid gap-4">
                  <label className="text-sm font-semibold text-ink">
                    Name
                    <Input
                      value={item.name}
                      onChange={(event) => onChange(kind, originalIndex, { ...item, name: event.target.value })}
                    />
                  </label>

                  {"url" in item ? (
                    <label className="text-sm font-semibold text-ink">
                      URL
                      <Input
                        value={item.url ?? ""}
                        onChange={(event) => onChange(kind, originalIndex, { ...item, url: event.target.value })}
                      />
                    </label>
                  ) : null}

                  {"channel_id" in item ? (
                    <label className="text-sm font-semibold text-ink">
                      Channel ID
                      <Input
                        value={item.channel_id ?? ""}
                        onChange={(event) =>
                          onChange(kind, originalIndex, { ...item, channel_id: event.target.value })
                        }
                      />
                    </label>
                  ) : null}

                  <label className="text-sm font-semibold text-ink">
                    Note Folder
                    <Input
                      value={item.note_folder}
                      onChange={(event) =>
                        onChange(kind, originalIndex, { ...item, note_folder: event.target.value })
                      }
                    />
                  </label>

                  <label className="flex items-center gap-3 rounded-[18px] border border-black/5 bg-parchment/80 px-4 py-3 text-sm font-semibold text-ink">
                    <input
                      checked={item.enabled}
                      onChange={(event) => onChange(kind, originalIndex, { ...item, enabled: event.target.checked })}
                      type="checkbox"
                    />
                    Enabled for digest collection
                  </label>
                </div>
              </div>
            );
          })
        ) : (
          <SectionBlurb>No entries match the current search and scope filters.</SectionBlurb>
        )}
      </div>
    </Panel>
  );
}

function SummaryCard({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-2 text-sm text-slate">{detail}</p>
    </div>
  );
}
