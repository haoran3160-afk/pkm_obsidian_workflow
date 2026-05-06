import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import Dashboard from "./Dashboard";

vi.mock("../store/appStore", () => ({
  useAppStore: () => ({
    status: {
      config_summary: { rss_count: 3, youtube_count: 2, write_mode: "disk", vault_path: "D:/vault" },
      run: { active: false, mode: "", started_at: "", finished_at: "", return_code: null, last_error: "", event_count: 0 },
      recent_outputs: [],
      feed_health: { run_date: "2026-04-21", counts: { ok: 5, warning: 1, error: 0 }, sources: [] }
    },
    fetchStatus: vi.fn(),
    fetchLogs: vi.fn(),
    appendLog: vi.fn(),
    logs: []
  })
}));

vi.mock("../lib/api", () => ({
  api: {
    runWorkflow: vi.fn().mockResolvedValue({ ok: true })
  },
  buildLogsUrl: () => "/api/logs/stream"
}));

describe("Dashboard", () => {
  it("renders quick run controls", () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    expect(screen.getByText("Operational Snapshot")).toBeInTheDocument();
    expect(screen.getByText("Dry Run")).toBeInTheDocument();
    expect(screen.getByText("Run Digest")).toBeInTheDocument();
  });
});
