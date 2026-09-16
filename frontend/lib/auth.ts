"use client";

import type { TokenResponse } from "./types";

const SESSION_CHANGE_EVENT = "scientific-data:session-changed";
const SESSION_DATA_STORAGE_KEYS = [
  "scientific_data_cache_v7",
  "global_metadata_v2",
  "global_filters_v3"
] as const;

function clearPersistedSessionData() {
  for (const key of SESSION_DATA_STORAGE_KEYS) window.localStorage.removeItem(key);
}

function publishSessionChange() {
  window.dispatchEvent(new Event(SESSION_CHANGE_EVENT));
}

export function subscribeSessionChange(listener: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== "token") return;
    clearPersistedSessionData();
    listener();
  };
  window.addEventListener(SESSION_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(SESSION_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function saveSession(session: TokenResponse) {
  clearPersistedSessionData();
  window.localStorage.setItem("token", session.access_token);
  window.localStorage.setItem(
    "profile",
    JSON.stringify({
      full_name: session.full_name,
      role: session.role,
      career_id: session.career_id
    })
  );
  publishSessionChange();
}

export function getProfile(): { full_name: string; role: string; career_id: number | null } | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem("profile");
  return raw ? JSON.parse(raw) : null;
}

export function clearSession() {
  window.localStorage.removeItem("token");
  window.localStorage.removeItem("profile");
  clearPersistedSessionData();
  publishSessionChange();
}
