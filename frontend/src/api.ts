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
