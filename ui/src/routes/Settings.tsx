import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { ActionButton, Panel, SectionBlurb, StatusBadge, TextArea } from "../components/shell";
import { useAppStore } from "../store/appStore";

export default function Settings() {
  const { status, output, fetchStatus, fetchOutput } = useAppStore();
  const [doctorOutput, setDoctorOutput] = useState("");
  const [doctorState, setDoctorState] = useState<"idle" | "running">("idle");

  useEffect(() => {
    void fetchStatus();
    void fetchOutput();
  }, [fetchOutput, fetchStatus]);

  async function runDoctor(skipNetwork: boolean) {
    setDoctorState("running");
    try {
      const result = await api.runDoctor(skipNetwork);
      setDoctorOutput(`${result.stdout}\n${result.stderr}`.trim());
      await Promise.all([fetchStatus(), fetchOutput()]);
    } finally {
      setDoctorState("idle");
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Runtime Posture"
        eyebrow="Settings"
        aside="This page is intentionally about environment confidence: run-state, output posture, and doctor diagnostics."
      >
        <div className="grid gap-4 lg:grid-cols-4">
          <InfoCard label="Active Run" value={status?.run.active ? status.run.mode : "Idle"} detail={status?.run.started_at || "No active run"} />
          <InfoCard label="Vault Path" value={output?.vault_path || "N/A"} detail={output?.write_mode || "No output config"} />
          <InfoCard label="LLM Copy" value={output?.enable_llm_copy ? "Enabled" : "Disabled"} detail={output?.curation_model || "N/A"} />
          <InfoCard label="Feed Health" value={status?.feed_health.run_date || "N/A"} detail={`warn=${status?.feed_health.counts.warning ?? 0} error=${status?.feed_health.counts.error ?? 0}`} />
        </div>
      </Panel>

      <Panel title="Doctor Controls" eyebrow="Diagnostics" aside="Use the network-skipping doctor first when you want a fast local posture check.">
        <div className="flex flex-wrap gap-3">
          <ActionButton disabled={doctorState === "running"} onClick={() => void runDoctor(true)}>
            {doctorState === "running" ? "Running..." : "Doctor (Skip Network)"}
          </ActionButton>
          <ActionButton className="bg-ember hover:bg-ember/90" disabled={doctorState === "running"} onClick={() => void runDoctor(false)}>
            {doctorState === "running" ? "Running..." : "Doctor (Full)"}
          </ActionButton>
          <StatusBadge tone={doctorState === "running" ? "live" : "idle"}>
            {doctorState === "running" ? "in progress" : "ready"}
          </StatusBadge>
        </div>
        <div className="mt-4 rounded-[20px] border border-black/5 bg-parchment/80 px-4 py-3">
          <SectionBlurb>
            Expected use: run doctor after changing vault path, API mode, env vars, or when the dashboard status looks stale.
          </SectionBlurb>
        </div>
      </Panel>

      <Panel title="Doctor Output" eyebrow="Runtime Trace" aside="The exact subprocess output is preserved here for copy-paste debugging and issue reporting.">
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
