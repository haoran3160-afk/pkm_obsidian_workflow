export type RunMode = "digest" | "raw" | "dry-run" | "test";

export type RunSnapshot = {
  active: boolean;
  mode: string;
  started_at: string;
  finished_at: string;
  return_code: number | null;
  last_error: string;
  event_count: number;
};

export type StatusResponse = {
  run: RunSnapshot;
  config_summary: {
    rss_count: number;
    youtube_count: number;
    write_mode: string;
    vault_path: string;
  };
  recent_outputs: Array<{ name: string; path: string; updated_at: string }>;
  feed_health: {
    run_date: string;
    counts: Record<string, number>;
    sources: Array<{ source: string; status: string; item_count: number; detail: string }>;
  };
};

export type FeedSource = {
  name: string;
  url?: string;
  channel_id?: string;
  domain?: string;
  note_folder: string;
  enabled: boolean;
  content_type?: string;
};

export type SourcesResponse = {
  rss_feeds: FeedSource[];
  youtube_channels: FeedSource[];
};

export type OutputConfig = {
  write_mode: "disk" | "api" | "both";
  vault_path: string;
  obsidian_api_base: string;
  obsidian_api_key: string;
  max_papers_per_day: number;
  max_videos_per_channel: number;
  max_paper_notes_per_day: number;
  max_video_notes_per_day: number;
  daily_digest_top_picks: number;
  daily_digest_max_items_per_source: number;
  daily_digest_action_items: number;
  daily_digest_max_deferred_items: number;
  daily_digest_only_output: boolean;
  enable_llm_copy: boolean;
  openai_api_key: string;
  openai_base_url: string;
  curation_model: string;
  curation_reasoning_effort: string;
};

export type LogEvent = {
  id?: number;
  ts?: string;
  kind: string;
  message: string;
};
