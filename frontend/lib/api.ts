import type {
  Career,
  DashboardKpi,
  GoalSummaryItem,
  ImportBatch,
  ImportedProgressReport,
  ImportJob,
  ImportStatus,
  Period,
  Participant,
  Project,
  Production,
  Teacher,
  TokenResponse
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

type ApiFetchErrorOptions = {
  status: number;
  code?: string;
  correlation_id?: string | null;
  details?: Record<string, unknown> | null;
};

export class ApiFetchError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly correlation_id: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(message: string, options: ApiFetchErrorOptions) {
    super(message);
    this.name = "ApiFetchError";
    this.status = options.status;
    this.code = options.code;
    this.correlation_id = options.correlation_id ?? null;
    this.details = options.details ?? null;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function buildApiFetchError(response: Response): Promise<ApiFetchError> {
  const body: unknown = await response.json().catch(() => null);
  if (
    isObject(body) &&
    typeof body.code === "string" &&
    typeof body.message === "string" &&
    typeof body.correlation_id === "string"
  ) {
    return new ApiFetchError(body.message, {
      status: response.status,
      code: body.code,
      correlation_id: body.correlation_id,
      details: isObject(body.details) ? body.details : null
    });
  }
  return new ApiFetchError("Request failed", { status: response.status });
}

export function getToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("token");
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    cache: "no-store"
  });

  if (!response.ok) {
    throw await buildApiFetchError(response);
  }
  return response.json();
}

export async function apiFetchBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    cache: "no-store"
  });

  if (!response.ok) {
    throw await buildApiFetchError(response);
  }
  return response.blob();
}

type ObjectUrlApi = Pick<typeof URL, "createObjectURL" | "revokeObjectURL">;

export function createAuthenticatedBlobUrlOwner(urlApi: ObjectUrlApi = URL) {
  let disposed = false;
  let requestGeneration = 0;
  const ownedUrls = new Set<string>();

  const revoke = (url: string | null) => {
    if (url && ownedUrls.delete(url)) urlApi.revokeObjectURL(url);
  };

  return {
    async replace(previousUrl: string | null, load: () => Promise<Blob>): Promise<string | null> {
      const generation = ++requestGeneration;
      const blob = await load();
      if (disposed || generation !== requestGeneration) return null;
      revoke(previousUrl);
      const nextUrl = urlApi.createObjectURL(blob);
      ownedUrls.add(nextUrl);
      return nextUrl;
    },
    revoke,
    dispose() {
      disposed = true;
      requestGeneration += 1;
      for (const url of ownedUrls) urlApi.revokeObjectURL(url);
      ownedUrls.clear();
    }
  };
}

export async function login(email: string, password: string) {
  return apiFetch<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export const api = {
  careers: () => apiFetch<Career[]>("/metadata/careers"),
  periods: () => apiFetch<Period[]>("/metadata/periods"),
  dashboardSummary: (yearLabel: string, cycle: number, careerId?: string) => {
    const query = new URLSearchParams({ year: yearLabel, cycle: String(cycle) });
    if (careerId) query.set("career_id", careerId);
    return apiFetch<DashboardKpi>(`/dashboard/summary?${query}`);
  },
  effectiveDataRevision: () => apiFetch<{ revision: number }>("/human-review/effective-data-revision"),
  goalSummary: (yearLabel: string, cycle: number, careerId?: string) => {
    const query = new URLSearchParams({ year_label: yearLabel, cycle: String(cycle) });
    if (careerId) query.set("career_id", careerId);
    return apiFetch<GoalSummaryItem[]>(`/goals/summary?${query}`);
  },
  latestImportStatus: () => apiFetch<ImportStatus>("/imports/latest/status"),
  importJobs: (query = "") => apiFetch<ImportJob[]>(`/imports/jobs${query}`),
  importBatches: () => apiFetch<ImportBatch[]>("/imports/batches"),
  importBatchJobs: (batchId: number) => apiFetch<ImportJob[]>(`/imports/batches/${batchId}/jobs`),
  importJobFile: (jobId: number, options: RequestInit = {}) =>
    apiFetchBlob(`/imports/jobs/${encodeURIComponent(String(jobId))}/file`, options),
  importedProgress: (query = "") => apiFetch<ImportedProgressReport[]>(`/imports/progress-records${query}`),
  importedProgressFile: (progressId: number, options: RequestInit = {}) =>
    apiFetchBlob(`/imports/progress-records/${encodeURIComponent(String(progressId))}/file`, options),
  teachers: (query = "") => apiFetch<Teacher[]>(`/teachers${query}`),
  participants: (query = "") => apiFetch<Participant[]>(`/participants${query}`),
  production: (query = "") => apiFetch<Production[]>(`/production${query}`),
  projects: (query = "") => apiFetch<Project[]>(`/projects${query}`)
};
