import type {
  LogEvent,
  OutputConfig,
  RunMode,
  SettingsEnvConfig,
  SourcesResponse,
  StatusResponse,
  SystemInfo
} from "../types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json"
    },
    ...init
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  getStatus: () => request<StatusResponse>("/api/status"),
  runWorkflow: (mode: RunMode) =>
    request<{ ok: boolean }>("/api/run", {
      method: "POST",
      body: JSON.stringify({ mode })
    }),
  runDoctor: (skipNetwork: boolean) =>
    request<{ ok: boolean; stdout: string; stderr: string; return_code: number }>("/api/doctor", {
      method: "POST",
      body: JSON.stringify({ skip_network: skipNetwork })
    }),
  getLogsHistory: () =>
    request<{ history: LogEvent[]; stream: LogEvent[] }>("/api/logs/history"),
  getSources: () => request<SourcesResponse>("/api/config/sources"),
  updateSources: (payload: SourcesResponse) =>
    request<SourcesResponse & { ok: boolean }>("/api/config/sources", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  getOutputConfig: () => request<OutputConfig>("/api/config/output"),
  updateOutputConfig: (payload: OutputConfig) =>
    request<OutputConfig & { ok: boolean }>("/api/config/output", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  getSettingsEnv: () => request<SettingsEnvConfig>("/api/settings/env"),
  updateSettingsEnv: (payload: SettingsEnvConfig) =>
    request<SettingsEnvConfig & { ok: boolean }>("/api/settings/env", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  getSystemInfo: () => request<SystemInfo>("/api/settings/system"),
  validateVault: (vaultPath: string) =>
    request<{ exists: boolean; is_dir: boolean; writable: boolean }>("/api/validate/vault", {
      method: "POST",
      body: JSON.stringify({ vault_path: vaultPath })
    })
};

export function buildLogsUrl() {
  return "/api/logs/stream";
}
