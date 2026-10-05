export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// Inside the embedded widget, requests carry the widget session (or anonymous
// visitor id) as headers instead of relying on cookies.
let widgetHeaders: Record<string, string> = {};

export function setWidgetAuth(auth: { token?: string; visitorId?: string } | null) {
  widgetHeaders = {};
  if (auth?.token) widgetHeaders.Authorization = `Bearer ${auth.token}`;
  if (auth?.visitorId) widgetHeaders["X-Visitor-Id"] = auth.visitorId;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...widgetHeaders, ...init.headers },
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Not JSON; keep the status text.
    }
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export interface User {
  email: string;
  name: string;
  is_admin: boolean;
}

export interface Person {
  id: string;
  email: string;
  name: string;
  username: string | null;
  is_admin: boolean;
  source: "local" | "embed";
  blocked: boolean;
  has_password: boolean;
  invite_pending: boolean;
  app_ids: string[];
  last_seen_at: string | null;
  created_at: string;
}

export interface InviteLink {
  user: Person;
  link: string;
  expires_at: string;
}

export interface MyApp {
  id: string;
  name: string;
  description: string;
}

export interface ProviderKey {
  needs_key: boolean;
  chat: boolean;
  default_model: string;
  default_classifier_model: string;
  provider: string;
  label: string;
  key_hint: string;
  configured: boolean;
  last4: string | null;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_error: string | null;
}

export interface ServiceStatus {
  name: string;
  ok: boolean;
  detail: string;
}

export interface ChatAppInput {
  name: string;
  description: string;
  welcome_message: string;
  provider: string;
  model: string;
  personality: string;
  knowledge_notes: string;
  in_scope: string;
  out_of_scope: string;
  refusal_message: string;
  strict_mode: boolean;
  max_answer_tokens: number;
  messages_per_minute: number;
  daily_token_limit: number;
  is_public: boolean;
  classifier_enabled: boolean;
  classifier_provider: string | null;
  classifier_model: string | null;
  classifier_strictness: "lenient" | "balanced" | "strict";
  classifier_fail_open: boolean;
  temperature: number | null;
  seed: number | null;
}

export interface ChatApp extends ChatAppInput {
  id: string;
  created_at: string;
  updated_at: string;
  source_count: number;
  conversation_count: number;
}

export interface AppInfo {
  id: string;
  name: string;
  description: string;
  welcome_message: string;
  is_owner: boolean;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  blocked?: boolean;
  error?: boolean;
  rating?: 1 | -1 | null;
  feedback?: string | null;
}

export type ChatEvent =
  | { type: "conversation"; id: string }
  | { type: "text"; text: string }
  | { type: "reset" }
  | { type: "tool"; label: string }
  | { type: "blocked"; message: string }
  | { type: "error"; message: string }
  | { type: "done"; message_id: number };

/** Sends a chat message and calls onEvent for each streamed event. */
export async function streamChat(
  appId: string,
  body: { message: string; conversation_id: string | null },
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/chat/${appId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...widgetHeaders },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    let message = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      // keep status text
    }
    throw new ApiError(response.status, message);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as ChatEvent);
      }
    }
  }
}

export interface DataSource {
  id: string;
  app_id: string;
  app_name: string | null;
  kind: string;
  name: string;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  details: Record<string, string | number | boolean | null>;
  created_at: string;
  updated_at: string;
}

/** Uploads files as multipart form data (no JSON content type). */
export async function uploadFiles(appId: string, files: File[]): Promise<DataSource[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const response = await fetch(`/api/apps/${appId}/sources/upload`, {
    method: "POST",
    credentials: "same-origin",
    body: form,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // keep status text
    }
    throw new ApiError(response.status, message);
  }
  return response.json();
}

export interface UsageReport {
  days: number;
  input_tokens: number;
  output_tokens: number;
  questions: number;
  blocked: number;
  conversations: number;
  cost: number;
  unpriced_models: string[];
  by_day: { date: string; input_tokens: number; output_tokens: number; cost: number }[];
  by_app: {
    app_id: string;
    name: string;
    input_tokens: number;
    output_tokens: number;
    questions: number;
    blocked: number;
    tokens_today: number;
    daily_token_limit: number;
    cost: number;
  }[];
  by_model: {
    kind: string;
    provider: string;
    model: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    cost: number | null;
  }[];
  recent_blocked: { app_id: string; app_name: string; question: string; verdict: string; reason: string; created_at: string }[];
}

export interface CloudSwitch {
  enabled: boolean;
  provider: "anthropic" | "openai" | "gemini";
  chat_model: string;
  classifier_model: string;
  local_app_count: number;
}

export interface UpdateStatus {
  current: string;
  managed: boolean;
  latest: string | null;
  available: boolean;
  status: string;
  message: string;
  checked_at: string | null;
  auto_update_at: string | null;
  release_url: string | null;
  requested: boolean;
  stalled: boolean;
}

export interface PromptVersionSummary {
  id: number;
  number: number;
  created_at: string;
  note: string;
  changed: string[];
  answers: number;
  helpful: number;
  not_helpful: number;
  test_passed: number | null;
  test_total: number | null;
  satisfaction: number | null;
  reviewed: number;
}

export interface PromptVersionDetail extends PromptVersionSummary {
  settings: Record<string, unknown>;
  system_prompt: string;
  template_version: string;
}

export interface RatedAnswer {
  message_id: number;
  conversation_id: string;
  question: string;
  answer: string;
  rating: 1 | -1;
  feedback: string | null;
  version_number: number | null;
  model: string | null;
  rated_at: string | null;
  test_case_id: string | null;
}

export interface TestCaseInput {
  question: string;
  expectation: "answer" | "refuse";
  must_include: string;
  reference_answer: string;
  notes: string;
}

export interface TestCase extends TestCaseInput {
  id: string;
  source_message_id: number | null;
  created_at: string;
  last_passed: boolean | null;
}

export interface TestRunSummary {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  version_number: number | null;
  total: number;
  passed: number;
  completed: number;
  regressions: number;
  fixed: number;
  created_at: string;
  finished_at: string | null;
}

export interface TestResultRow {
  case_id: string;
  question: string;
  expectation: "answer" | "refuse";
  passed: boolean;
  blocked: boolean;
  answer: string;
  failures: string[];
  seconds: number | null;
  change: "regressed" | "fixed" | "new" | "same";
}

export interface ModelPrice {
  provider: string;
  model: string;
  input: number | null;
  output: number | null;
  source: "builtin" | "custom" | "missing";
}

export interface ReviewSummary {
  available: boolean;
  reviewed: number;
  waiting: number;
  average: number | null;
  resolved_rate: number | null;
}

export interface ReviewedConversation {
  id: string;
  title: string;
  satisfaction: number;
  resolved: number | null;
  messages: number;
  version_number: number | null;
  reviewed_at: string;
  updated_at: string;
}

export interface TranscriptMessage {
  role: "user" | "assistant";
  content: string;
  blocked: boolean;
  rating: 1 | -1 | null;
  feedback: string | null;
  created_at: string;
}

export interface EmbedSettings {
  enabled: boolean;
  auth: "login" | "signed" | "public";
  allowed_domains: string[];
  color: string;
}

export interface EmbedInfo extends EmbedSettings {
  app_id: string;
  has_secret: boolean;
  script_url: string;
  snippet: string;
  public_url_configured: boolean;
}

export interface WidgetConfig {
  name: string;
  description: string;
  welcome_message: string;
  color: string;
  auth: "login" | "signed" | "public";
  allowed_origins: string[];
}
