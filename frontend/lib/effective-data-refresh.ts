export const EFFECTIVE_DATA_CHANGED_EVENT = "human-review:effective-data-changed";

export function publishEffectiveDataRefresh(): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(new Event(EFFECTIVE_DATA_CHANGED_EVENT));
}

export function subscribeEffectiveDataRefresh(listener: () => void): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return () => undefined;
  window.addEventListener(EFFECTIVE_DATA_CHANGED_EVENT, listener);
  return () => window.removeEventListener(EFFECTIVE_DATA_CHANGED_EVENT, listener);
}
