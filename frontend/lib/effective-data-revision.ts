import { ApiFetchError } from "./api";

type RevisionResponse = { revision: number };

type WindowLike = {
  document?: { visibilityState?: string };
  addEventListener: (type: "focus" | "visibilitychange", listener: () => void) => void;
  removeEventListener: (type: "focus" | "visibilitychange", listener: () => void) => void;
  setInterval: (listener: () => void, intervalMs: number) => ReturnType<typeof setInterval>;
  clearInterval: (id: ReturnType<typeof setInterval>) => void;
};

type RevisionPollingOptions = {
  readRevision: () => Promise<RevisionResponse>;
  onChanged: (revision: number) => void;
  getSessionKey?: () => string | null;
  intervalMs?: number;
  windowLike?: WindowLike | null;
};

const DEFAULT_INTERVAL_MS = 3_000;

function browserWindow(): WindowLike | null {
  return typeof window === "undefined" ? null : window;
}

function isUnauthorized(error: unknown) {
  return error instanceof ApiFetchError && (error.status === 401 || error.status === 403);
}

export function startEffectiveDataRevisionPolling({
  readRevision,
  onChanged,
  getSessionKey = () => "active",
  intervalMs = DEFAULT_INTERVAL_MS,
  windowLike = browserWindow()
}: RevisionPollingOptions): () => void {
  if (!windowLike) return () => undefined;

  let latestRevision: number | null = null;
  let blockedSession: string | null = null;
  let inFlight = false;
  let stopped = false;

  const check = () => {
    if (stopped || inFlight || windowLike.document?.visibilityState === "hidden") return;
    const sessionKey = getSessionKey();
    if (!sessionKey || blockedSession === sessionKey) return;
    inFlight = true;
    void readRevision()
      .then(({ revision }) => {
        if (stopped) return;
        if (latestRevision !== null && revision !== latestRevision) onChanged(revision);
        latestRevision = revision;
        blockedSession = null;
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) blockedSession = sessionKey;
      })
      .finally(() => {
        inFlight = false;
      });
  };

  const onVisibilityChange = () => {
    if (windowLike.document?.visibilityState !== "hidden") check();
  };

  const interval = windowLike.setInterval(check, intervalMs);
  windowLike.addEventListener("focus", check);
  windowLike.addEventListener("visibilitychange", onVisibilityChange);
  check();

  return () => {
    stopped = true;
    windowLike.clearInterval(interval);
    windowLike.removeEventListener("focus", check);
    windowLike.removeEventListener("visibilitychange", onVisibilityChange);
  };
}
