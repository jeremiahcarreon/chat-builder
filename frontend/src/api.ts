export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init.headers },
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
  is_admin: boolean;
}

export interface ProviderKey {
  needs_key: boolean;
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
  in_scope: string;
  out_of_scope: string;
  refusal_message: string;
  strict_mode: boolean;
  max_answer_tokens: number;
  messages_per_minute: number;
  daily_token_limit: number;
  is_public: boolean;
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
    headers: { "Content-Type": "application/json" },
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
