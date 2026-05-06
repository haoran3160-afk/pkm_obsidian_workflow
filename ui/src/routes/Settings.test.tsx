import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import Settings from "./Settings";

const fetchStatus = vi.fn().mockResolvedValue(undefined);
const fetchOutput = vi.fn().mockResolvedValue(undefined);

vi.mock("../store/appStore", () => ({
  useAppStore: () => ({
    status: {
      config_summary: { rss_count: 3, youtube_count: 2, write_mode: "disk", vault_path: "D:/vault" },
      run: {
        active: false,
        mode: "",
        started_at: "",
        finished_at: "",
        return_code: null,
        last_error: "",
        event_count: 0
      },
      recent_outputs: [],
      feed_health: { run_date: "2026-04-21", counts: { ok: 5, warning: 1, error: 0 }, sources: [] }
    },
    output: {
      write_mode: "disk",
      vault_path: "D:/vault",
      obsidian_api_base: "http://127.0.0.1:27123",
      obsidian_api_key: "",
      max_papers_per_day: 10,
      max_videos_per_channel: 3,
      max_paper_notes_per_day: 7,
      max_video_notes_per_day: 27,
      daily_digest_top_picks: 3,
      daily_digest_max_items_per_source: 3,
      daily_digest_action_items: 3,
      daily_digest_max_deferred_items: 10,
      daily_digest_only_output: true,
      enable_llm_copy: false,
      openai_api_key: "",
      openai_base_url: "",
      curation_model: "gpt-5.4-mini",
      curation_reasoning_effort: "medium"
    },
    fetchStatus,
    fetchOutput
  })
}));

vi.mock("../lib/api", () => ({
  api: {
    getSettingsEnv: vi.fn().mockResolvedValue({
      obsidian_api_base: "http://127.0.0.1:27123",
      obsidian_api_key: "",
      openai_api_key: "",
      openai_base_url: "",
      curation_model: "gpt-5.4-mini",
      curation_reasoning_effort: "medium"
    }),
    getSystemInfo: vi.fn().mockResolvedValue({
      workspace_root: "D:/Agent_programs/obsidian_workflow_open",
      python_executable: "python",
      python_version: "3.11.0",
      platform: "win32",
      config: { path: "D:/Agent_programs/obsidian_workflow_open/pkm_config.json", exists: true, updated_at: "" },
      env: { path: "D:/Agent_programs/obsidian_workflow_open/.env", exists: true, updated_at: "" },
      log: { path: "D:/Agent_programs/obsidian_workflow_open/fetch.log", exists: true, updated_at: "" },
      source_health: {
        path: "D:/Agent_programs/obsidian_workflow_open/source_health.json",
        exists: true,
        updated_at: ""
      },
      vault: { path: "D:/vault", exists: true, is_dir: true, writable: true }
    }),
    runDoctor: vi.fn().mockResolvedValue({ ok: true, stdout: "ok", stderr: "", return_code: 0 }),
    updateSettingsEnv: vi.fn().mockResolvedValue({
      ok: true,
      obsidian_api_base: "http://127.0.0.1:27123",
      obsidian_api_key: "",
      openai_api_key: "",
      openai_base_url: "",
      curation_model: "gpt-5.4-mini",
      curation_reasoning_effort: "medium"
    })
  }
}));

describe("Settings", () => {
  it("renders environment and system sections", async () => {
    render(
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    );

    expect(screen.getByText("Runtime Posture")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("System Footprint")).toBeInTheDocument());
    expect(screen.getByText("Environment Overrides")).toBeInTheDocument();
    expect(screen.getByDisplayValue("http://127.0.0.1:27123")).toBeInTheDocument();
  });
});
