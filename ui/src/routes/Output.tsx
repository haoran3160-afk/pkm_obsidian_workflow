import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { ActionButton, GhostButton, Input, Panel, SectionBlurb, Select, StatusBadge } from "../components/shell";
import { useAppStore } from "../store/appStore";
import type { OutputConfig } from "../types";

export default function Output() {
  const { output, fetchOutput } = useAppStore();
  const [draft, setDraft] = useState<OutputConfig | null>(null);
  const [vaultCheck, setVaultCheck] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");

  useEffect(() => {
    void fetchOutput();
  }, [fetchOutput]);

  useEffect(() => {
    if (output) {
      setDraft(output);
      setSaveState("idle");
    }
  }, [output]);

  const dirty = Boolean(draft && output && JSON.stringify(draft) !== JSON.stringify(output));

  async function save() {
    if (!draft) {
      return;
    }
    setSaveState("saving");
    await api.updateOutputConfig(draft);
    await fetchOutput();
    setSaveState("saved");
  }

  async function validateVault() {
    if (!draft) {
      return;
    }
    const result = await api.validateVault(draft.vault_path);
    setVaultCheck(`exists=${result.exists} dir=${result.is_dir} writable=${result.writable}`);
  }

  if (!draft) {
    return <Panel title="Output Settings">Loading...</Panel>;
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Delivery Posture"
        eyebrow="Output"
        aside="This is the control surface for where artifacts land, how writes happen, and whether the final copy should stay deterministic or use the optional LLM layer."
      >
        <div className="grid gap-4 lg:grid-cols-4">
          <SurfaceCard label="Write Mode" value={draft.write_mode} detail="disk / api / both" />
          <SurfaceCard label="Vault Target" value="Active" detail={draft.vault_path} />
          <SurfaceCard label="LLM Copy" value={draft.enable_llm_copy ? "On" : "Off"} detail={draft.curation_model} />
          <SurfaceCard
            label="Save State"
            value={dirty ? "Pending" : saveState === "saved" ? "Saved" : "Synced"}
            detail="Config + env backed"
          />
        </div>
      </Panel>

      <div className="sticky top-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[24px] border border-black/5 bg-white/90 px-4 py-3 shadow-panel backdrop-blur">
        <div className="flex items-center gap-3">
          <StatusBadge tone={dirty ? "warn" : saveState === "saved" ? "live" : "idle"}>
            {dirty ? "pending changes" : saveState === "saved" ? "saved" : "synced"}
          </StatusBadge>
          <SectionBlurb>Output settings write to both `pkm_config.json` and `.env` where required.</SectionBlurb>
        </div>
        <div className="flex flex-wrap gap-3">
          <GhostButton onClick={() => void validateVault()}>Validate Vault</GhostButton>
          <ActionButton disabled={!dirty || saveState === "saving"} onClick={() => void save()}>
            {saveState === "saving" ? "Saving..." : "Save Output Settings"}
          </ActionButton>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Panel title="Vault & Delivery" eyebrow="Routing">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm font-semibold text-ink">
              Write Mode
              <Select
                value={draft.write_mode}
                onChange={(event) => {
                  setDraft({ ...draft, write_mode: event.target.value as OutputConfig["write_mode"] });
                  setSaveState("idle");
                }}
              >
                <option value="disk">disk</option>
                <option value="api">api</option>
                <option value="both">both</option>
              </Select>
            </label>
            <label className="text-sm font-semibold text-ink">
              Vault Path
              <Input
                value={draft.vault_path}
                onChange={(event) => {
                  setDraft({ ...draft, vault_path: event.target.value });
                  setSaveState("idle");
                }}
              />
            </label>
            <label className="text-sm font-semibold text-ink">
              Obsidian API Base
              <Input
                value={draft.obsidian_api_base}
                onChange={(event) => {
                  setDraft({ ...draft, obsidian_api_base: event.target.value });
                  setSaveState("idle");
                }}
              />
            </label>
            <label className="text-sm font-semibold text-ink">
              Obsidian API Key
              <Input
                value={draft.obsidian_api_key}
                onChange={(event) => {
                  setDraft({ ...draft, obsidian_api_key: event.target.value });
                  setSaveState("idle");
                }}
              />
            </label>
          </div>
          {vaultCheck ? (
            <div className="mt-4 rounded-[20px] border border-black/5 bg-parchment/80 px-4 py-3 text-sm text-slate">{vaultCheck}</div>
          ) : null}
        </Panel>

        <Panel title="Digest Limits" eyebrow="Curation">
          <div className="grid gap-4 md:grid-cols-2">
            <NumericInput
              label="Top Picks"
              value={draft.daily_digest_top_picks}
              onChange={(value) => {
                setDraft({ ...draft, daily_digest_top_picks: value });
                setSaveState("idle");
              }}
            />
            <NumericInput
              label="Per Source Limit"
              value={draft.daily_digest_max_items_per_source}
              onChange={(value) => {
                setDraft({ ...draft, daily_digest_max_items_per_source: value });
                setSaveState("idle");
              }}
            />
            <NumericInput
              label="Action Items"
              value={draft.daily_digest_action_items}
              onChange={(value) => {
                setDraft({ ...draft, daily_digest_action_items: value });
                setSaveState("idle");
              }}
            />
            <NumericInput
              label="Deferred Limit"
              value={draft.daily_digest_max_deferred_items}
              onChange={(value) => {
                setDraft({ ...draft, daily_digest_max_deferred_items: value });
                setSaveState("idle");
              }}
            />
            <NumericInput
              label="Max Paper Notes / Day"
              value={draft.max_paper_notes_per_day}
              onChange={(value) => {
                setDraft({ ...draft, max_paper_notes_per_day: value });
                setSaveState("idle");
              }}
            />
            <NumericInput
              label="Max Video Notes / Day"
              value={draft.max_video_notes_per_day}
              onChange={(value) => {
                setDraft({ ...draft, max_video_notes_per_day: value });
                setSaveState("idle");
              }}
            />
          </div>
          <label className="mt-4 flex items-center gap-3 rounded-[18px] border border-black/5 bg-parchment/80 px-4 py-3 text-sm font-semibold text-ink">
            <input
              checked={draft.daily_digest_only_output}
              onChange={(event) => {
                setDraft({ ...draft, daily_digest_only_output: event.target.checked });
                setSaveState("idle");
              }}
              type="checkbox"
            />
            Keep single-core daily digest output only
          </label>
        </Panel>
      </div>

      <Panel title="LLM Copy Layer" eyebrow="Optional" aside="The LLM layer is opt-in and only affects the final copy, not source selection or evidence capture.">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex items-center gap-3 rounded-[18px] border border-black/5 bg-parchment/80 px-4 py-3 text-sm font-semibold text-ink">
            <input
              checked={draft.enable_llm_copy}
              onChange={(event) => {
                setDraft({ ...draft, enable_llm_copy: event.target.checked });
                setSaveState("idle");
              }}
              type="checkbox"
            />
            Enable LLM digest copy refinement
          </label>
          <div className="hidden md:block" />
          <label className="text-sm font-semibold text-ink">
            Model
            <Input
              value={draft.curation_model}
              onChange={(event) => {
                setDraft({ ...draft, curation_model: event.target.value });
                setSaveState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            Reasoning Effort
            <Select
              value={draft.curation_reasoning_effort}
              onChange={(event) => {
                setDraft({ ...draft, curation_reasoning_effort: event.target.value });
                setSaveState("idle");
              }}
            >
              <option value="minimal">minimal</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </Select>
          </label>
          <label className="text-sm font-semibold text-ink">
            OpenAI Base URL
            <Input
              value={draft.openai_base_url}
              onChange={(event) => {
                setDraft({ ...draft, openai_base_url: event.target.value });
                setSaveState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            OpenAI API Key
            <Input
              value={draft.openai_api_key}
              onChange={(event) => {
                setDraft({ ...draft, openai_api_key: event.target.value });
                setSaveState("idle");
              }}
            />
          </label>
        </div>
      </Panel>
    </div>
  );
}

function NumericInput({
  label,
  value,
  onChange
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="text-sm font-semibold text-ink">
      {label}
      <Input min={0} type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function SurfaceCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">{label}</p>
      <p className="mt-3 text-2xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-2 break-all text-sm text-slate">{detail}</p>
    </div>
  );
}
