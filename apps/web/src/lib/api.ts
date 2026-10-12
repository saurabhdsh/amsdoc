import { useAuth, type User } from "./auth";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "The request failed.";
}

export async function api<T>(path: string, init: RequestInit & { idempotent?: boolean } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = useAuth.getState().token;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (init.idempotent) headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, { ...init, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const error = data?.error ?? {};
    throw new ApiError(response.status, error.code ?? "ERROR", error.message ?? response.statusText, error.details ?? null);
  }
  return data as T;
}

export type { User };
