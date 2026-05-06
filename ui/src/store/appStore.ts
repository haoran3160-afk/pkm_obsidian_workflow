import { create } from "zustand";

import { api } from "../lib/api";
import type { LogEvent, OutputConfig, SourcesResponse, StatusResponse } from "../types";

type AppState = {
  status: StatusResponse | null;
  sources: SourcesResponse | null;
  output: OutputConfig | null;
  logs: LogEvent[];
  loading: boolean;
  error: string;
  fetchStatus: () => Promise<void>;
  fetchSources: () => Promise<void>;
  fetchOutput: () => Promise<void>;
  fetchLogs: () => Promise<void>;
  setLogs: (logs: LogEvent[]) => void;
  appendLog: (event: LogEvent) => void;
  clearError: () => void;
};

export const useAppStore = create<AppState>((set) => ({
  status: null,
  sources: null,
  output: null,
  logs: [],
  loading: false,
  error: "",
  fetchStatus: async () => {
    set({ loading: true, error: "" });
    try {
      set({ status: await api.getStatus(), loading: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unknown error", loading: false });
    }
  },
  fetchSources: async () => {
    set({ loading: true, error: "" });
    try {
      set({ sources: await api.getSources(), loading: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unknown error", loading: false });
    }
  },
  fetchOutput: async () => {
    set({ loading: true, error: "" });
    try {
      set({ output: await api.getOutputConfig(), loading: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unknown error", loading: false });
    }
  },
  fetchLogs: async () => {
    set({ loading: true, error: "" });
    try {
      const payload = await api.getLogsHistory();
      set({ logs: [...payload.history, ...payload.stream], loading: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unknown error", loading: false });
    }
  },
  setLogs: (logs) => set({ logs }),
  appendLog: (event) => set((state) => ({ logs: [...state.logs.slice(-199), event] })),
  clearError: () => set({ error: "" })
}));
