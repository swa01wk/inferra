export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  status: "active" | "revoked";
  is_admin: boolean;
  expires_at: string | null;
  created_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  secret: string;
}

export interface Adapter {
  id: string;
  name: string;
  rank: number;
  status: "registered" | "downloading" | "available" | "loaded" | "active" | "failed" | "deleted";
  storage_uri: string;
  error_message?: string | null;
}

export interface Worker {
  id: string;
  hostname: string;
  gpu_type: string;
  gpu_vram_mb: number;
  endpoint: string;
  status: string;
}

export interface Deployment {
  id: string;
  model_id: string;
  worker_id: string;
  endpoint: string;
  status: string;
  config_json: Record<string, unknown> | null;
}

export interface UsageRequest {
  request_id: string;
  logical_model: string;
  status: string;
  received_at: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  ttft_ms: number | null;
  total_ms: number | null;
}

export interface UsageSummary {
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  requests: UsageRequest[];
}

export interface Model {
  id: string;
  object: string;
}
