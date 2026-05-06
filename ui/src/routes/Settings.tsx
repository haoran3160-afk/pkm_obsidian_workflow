import { useEffect, useState } from "react";

import { api } from "../lib/api";
import {
  ActionButton,
  GhostButton,
  Input,
  Panel,
  SectionBlurb,
  Select,
  StatusBadge,
  TextArea
} from "../components/shell";
import { useAppStore } from "../store/appStore";
import type { SettingsEnvConfig, SystemInfo } from "../types";

const envDefaults: SettingsEnvConfig = {
  obsidian_api_base: "",
  obsidian_api_key: "",
  openai_api_key: "",
  openai_base_url: "",
  curation_model: "gpt-5.4-mini",
  curation_reasoning_effort: "medium"
};

export default function Settings() {
  const { status, output, fetchStatus, fetchOutput } = useAppStore();
  const [doctorOutput, setDoctorOutput] = useState("");
  const [doctorState, setDoctorState] = useState<"idle" | "running">("idle");
  const [envDraft, setEnvDraft] = useState<SettingsEnvConfig>(envDefaults);
  const [savedEnv, setSavedEnv] = useState<SettingsEnvConfig>(envDefaults);
  const [envState, setEnvState] = useState<"idle" | "saving" | "saved">("idle");
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    void fetchStatus();
    void fetchOutput();
    void refreshLocalState();
  }, [fetchOutput, fetchStatus]);

  async function refreshLocalState() {
    const [envConfig, runtimeInfo] = await Promise.all([api.getSettingsEnv(), api.getSystemInfo()]);
    setEnvDraft(envConfig);
    setSavedEnv(envConfig);
    setSystemInfo(runtimeInfo);
    setEnvState("idle");
  }

  async function runDoctor(skipNetwork: boolean) {
    setDoctorState("running");
    try {
      const result = await api.runDoctor(skipNetwork);
      setDoctorOutput(`${result.stdout}\n${result.stderr}`.trim());
      await Promise.all([fetchStatus(), fetchOutput(), refreshLocalState()]);
    } finally {
      setDoctorState("idle");
    }
  }

  async function saveEnv() {
    setEnvState("saving");
    try {
      const next = await api.updateSettingsEnv(envDraft);
      setEnvDraft(next);
      setSavedEnv(next);
      setEnvState("saved");
      await Promise.all([fetchStatus(), fetchOutput(), refreshLocalState()]);
    } finally {
      setEnvState((current) => (current === "saving" ? "idle" : current));
    }
  }

  const envDirty = JSON.stringify(envDraft) !== JSON.stringify(savedEnv);

  return (
    <div className="space-y-6">
      <Panel
        title="Runtime Posture"
        eyebrow="Settings"
        aside="This page is for operator confidence: system footprint, raw environment overrides, and doctor diagnostics."
      >
        <div className="grid gap-4 lg:grid-cols-4">
          <InfoCard
            label="Active Run"
            value={status?.run.active ? status.run.mode : "Idle"}
            detail={status?.run.started_at || "No active run"}
          />
          <InfoCard
            label="Vault Path"
            value={output?.vault_path || "N/A"}
            detail={systemInfo?.vault.writable ? "writable target" : output?.write_mode || "No output config"}
          />
          <InfoCard
            label="LLM Copy"
            value={output?.enable_llm_copy ? "Enabled" : "Disabled"}
            detail={output?.curation_model || "N/A"}
          />
          <InfoCard
            label="Feed Health"
            value={status?.feed_health.run_date || "N/A"}
            detail={`warn=${status?.feed_health.counts.warning ?? 0} error=${status?.feed_health.counts.error ?? 0}`}
          />
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <Panel
          title="System Footprint"
          eyebrow="Host"
          aside="These are the exact local paths and runtime binaries the control plane is operating against."
        >
          <div className="grid gap-4 md:grid-cols-2">
            <PathCard
              label="Workspace"
              path={systemInfo?.workspace_root || "Loading..."}
              detail={`Python ${systemInfo?.python_version || "?"} on ${systemInfo?.platform || "?"}`}
            />
            <PathCard
              label="Python"
              path={systemInfo?.python_executable || "Loading..."}
              detail="Interpreter used for CLI-triggered workflow runs"
            />
            <PathCard
              label="Config"
              path={systemInfo?.config.path || "Loading..."}
              detail={systemInfo?.config.updated_at || "No timestamp"}
            />
            <PathCard
              label="Env"
              path={systemInfo?.env.path || "Loading..."}
              detail={systemInfo?.env.updated_at || "No timestamp"}
            />
            <PathCard
              label="Log"
              path={systemInfo?.log.path || "Loading..."}
              detail={systemInfo?.log.updated_at || "No timestamp"}
            />
            <PathCard
              label="Vault State"
              path={systemInfo?.vault.path || "No vault path"}
              detail={
                systemInfo
                  ? `exists=${String(systemInfo.vault.exists)} dir=${String(systemInfo.vault.is_dir)} writable=${String(systemInfo.vault.writable)}`
                  : "Loading..."
              }
            />
          </div>
        </Panel>

        <Panel
          title="Doctor Controls"
          eyebrow="Diagnostics"
          aside="Use skip-network for a fast posture check, then escalate to full doctor when feed/network behavior looks wrong."
        >
          <div className="flex flex-wrap gap-3">
            <ActionButton disabled={doctorState === "running"} onClick={() => void runDoctor(true)}>
              {doctorState === "running" ? "Running..." : "Doctor (Skip Network)"}
            </ActionButton>
            <ActionButton
              className="bg-ember hover:bg-ember/90"
              disabled={doctorState === "running"}
              onClick={() => void runDoctor(false)}
            >
              {doctorState === "running" ? "Running..." : "Doctor (Full)"}
            </ActionButton>
            <GhostButton disabled={doctorState === "running"} onClick={() => void refreshLocalState()}>
              Refresh Runtime
            </GhostButton>
            <StatusBadge tone={doctorState === "running" ? "live" : "idle"}>
              {doctorState === "running" ? "in progress" : "ready"}
            </StatusBadge>
          </div>
          <div className="mt-4 rounded-[20px] border border-black/5 bg-parchment/80 px-4 py-3">
            <SectionBlurb>
              Expected use: run doctor after changing vault path, provider keys, base URLs, or when dashboard status and log tape disagree.
            </SectionBlurb>
          </div>
        </Panel>
      </div>

      <div className="sticky top-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[24px] border border-black/5 bg-white/90 px-4 py-3 shadow-panel backdrop-blur">
        <div className="flex items-center gap-3">
          <StatusBadge tone={envDirty ? "warn" : envState === "saved" ? "live" : "idle"}>
            {envDirty ? "pending env edits" : envState === "saved" ? "saved" : "synced"}
          </StatusBadge>
          <SectionBlurb>
            Output owns routing and limits; this section is for runtime overrides and provider credentials.
          </SectionBlurb>
        </div>
        <div className="flex flex-wrap gap-2">
          <GhostButton onClick={() => setEnvDraft(savedEnv)}>Reset Draft</GhostButton>
          <ActionButton disabled={!envDirty || envState === "saving"} onClick={() => void saveEnv()}>
            {envState === "saving" ? "Saving..." : "Save Env Overrides"}
          </ActionButton>
        </div>
      </div>

      <Panel
        title="Environment Overrides"
        eyebrow="Env"
        aside="These values are written back to `.env` without changing the config schema. Use this when secrets or provider endpoints need to rotate."
      >
        <div className="grid gap-5 xl:grid-cols-2">
          <label className="text-sm font-semibold text-ink">
            Obsidian API Base
            <Input
              value={envDraft.obsidian_api_base}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, obsidian_api_base: event.target.value });
                setEnvState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            Obsidian API Key
            <Input
              type="password"
              value={envDraft.obsidian_api_key}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, obsidian_api_key: event.target.value });
                setEnvState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            OpenAI Base URL
            <Input
              value={envDraft.openai_base_url}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, openai_base_url: event.target.value });
                setEnvState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            OpenAI API Key
            <Input
              type="password"
              value={envDraft.openai_api_key}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, openai_api_key: event.target.value });
                setEnvState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            Curation Model
            <Input
              value={envDraft.curation_model}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, curation_model: event.target.value });
                setEnvState("idle");
              }}
            />
          </label>
          <label className="text-sm font-semibold text-ink">
            Reasoning Effort
            <Select
              value={envDraft.curation_reasoning_effort}
              onChange={(event) => {
                setEnvDraft({ ...envDraft, curation_reasoning_effort: event.target.value });
                setEnvState("idle");
              }}
            >
              <option value="minimal">minimal</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </Select>
          </label>
        </div>
      </Panel>

      <Panel
        title="Doctor Output"
        eyebrow="Runtime Trace"
        aside="The exact subprocess output is preserved here for copy-paste debugging and issue reporting."
      >
        <TextArea readOnly rows={22} value={doctorOutput || "No doctor output captured yet."} />
      </Panel>
    </div>
  );
}

function InfoCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">{label}</p>
      <p className="mt-3 break-all text-2xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-2 text-sm text-slate">{detail}</p>
    </div>
  );
}

function PathCard({ label, path, detail }: { label: string; path: string; detail: string }) {
  return (
    <div className="rounded-[24px] border border-black/5 bg-white/88 px-4 py-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate">{label}</p>
      <p className="mt-3 break-all text-base font-semibold tracking-tight text-ink">{path}</p>
      <p className="mt-2 text-sm text-slate">{detail}</p>
    </div>
  );
}
