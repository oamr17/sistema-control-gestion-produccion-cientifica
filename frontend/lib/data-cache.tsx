"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { api, getToken } from "./api";
import { subscribeSessionChange } from "./auth";
import { publishEffectiveDataRefresh, subscribeEffectiveDataRefresh } from "./effective-data-refresh";
import { startEffectiveDataRevisionPolling } from "./effective-data-revision";

export type CacheEntry<T> = {
  data: T;
  updatedAt: number;
};

export type DataCacheStore = {
  get: <T>(key: string) => CacheEntry<T> | undefined;
  set: <T>(key: string, data: T) => void;
  getInflight: <T>(key: string) => Promise<T> | undefined;
  setInflight: <T>(key: string, promise: Promise<T>) => void;
  clearInflight: (key: string, promise: Promise<unknown>) => void;
  getVersion: (key: string) => number;
  getSessionRevision: () => number;
  resetSession: () => void;
  invalidate: (prefix: string) => void;
  invalidateMany: (prefixes: readonly string[]) => void;
  hydrate: (entries: [string, CacheEntry<unknown>][]) => void;
  entries: () => [string, CacheEntry<unknown>][];
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => number;
};

export type DataCacheContextValue = Omit<DataCacheStore, "hydrate" | "entries" | "subscribe" | "getSnapshot"> & {
  revision: number;
  sessionRevision: number;
};

type CachedQueryOptions = {
  enabled?: boolean;
  staleTimeMs?: number;
};

type CachedQueryState<T> = {
  data: T | undefined;
  error: Error | null;
  isInitialLoading: boolean;
  isUpdating: boolean;
};

type KeyedCachedQueryState<T> = CachedQueryState<T> & {
  key: string;
  sessionRevision: number;
};

const DataCacheContext = createContext<DataCacheContextValue | null>(null);
const DEFAULT_STALE_TIME_MS = 5 * 60 * 1000;
const STORAGE_KEY = "scientific_data_cache_v7";
const HUMAN_REVIEW_PREFIX = "human-review:";
const EFFECTIVE_DATA_CACHE_PREFIXES = [
  HUMAN_REVIEW_PREFIX,
  "dashboard:",
  "kpis:",
  "canonical-participants:",
  "imported-progress:",
  "production:",
  "projects:",
  "research-entities:",
  "entities:",
  "goals:"
] as const;

export function invalidateEffectiveDataCaches(
  cache: Pick<DataCacheStore, "invalidateMany">
): void {
  cache.invalidateMany(EFFECTIVE_DATA_CACHE_PREFIXES);
}

export function createDataCacheStore(getSessionKey: () => string | null = () => null): DataCacheStore {
  const cache = new Map<string, CacheEntry<unknown>>();
  const inflight = new Map<string, Promise<unknown>>();
  const versions = new Map<string, number>();
  const listeners = new Set<() => void>();
  let revision = 0;
  let sessionKey = getSessionKey();
  let sessionRevision = 0;
  let versionSequence = 0;
  let sessionVersion = 0;

  const clearPrefix = (prefix: string) => {
    const keys = new Set([...cache.keys(), ...inflight.keys()]);
    for (const key of keys) {
      if (!key.startsWith(prefix)) continue;
      cache.delete(key);
      inflight.delete(key);
      versionSequence += 1;
      versions.set(key, versionSequence);
    }
  };

  const clearSessionState = () => {
    cache.clear();
    inflight.clear();
    versions.clear();
    versionSequence += 1;
    sessionVersion = versionSequence;
    sessionRevision += 1;
  };

  const syncSession = () => {
    const nextSessionKey = getSessionKey();
    if (nextSessionKey === sessionKey) return false;
    sessionKey = nextSessionKey;
    clearSessionState();
    revision += 1;
    return true;
  };

  const notify = () => {
    for (const listener of listeners) listener();
  };

  const invalidateMany = (prefixes: readonly string[]) => {
    syncSession();
    for (const prefix of prefixes) clearPrefix(prefix);
    revision += 1;
    notify();
  };

  return {
    get: (key) => {
      syncSession();
      return cache.get(key) as CacheEntry<never> | undefined;
    },
    set: (key, data) => {
      syncSession();
      cache.set(key, { data, updatedAt: Date.now() });
    },
    getInflight: (key) => {
      syncSession();
      return inflight.get(key) as Promise<never> | undefined;
    },
    setInflight: (key, promise) => {
      syncSession();
      inflight.set(key, promise);
    },
    clearInflight: (key, promise) => {
      syncSession();
      if (inflight.get(key) === promise) inflight.delete(key);
    },
    getVersion: (key) => {
      syncSession();
      return versions.get(key) ?? sessionVersion;
    },
    getSessionRevision: () => {
      syncSession();
      return sessionRevision;
    },
    resetSession: () => {
      sessionKey = getSessionKey();
      clearSessionState();
      revision += 1;
      notify();
    },
    invalidate: (prefix) => invalidateMany([prefix]),
    invalidateMany,
    hydrate: (entries) => {
      syncSession();
      cache.clear();
      for (const [key, entry] of entries) {
        if (!key.startsWith(HUMAN_REVIEW_PREFIX)) cache.set(key, entry);
      }
    },
    entries: () => {
      syncSession();
      return [...cache.entries()].filter(([key]) => !key.startsWith(HUMAN_REVIEW_PREFIX));
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => {
      syncSession();
      return revision;
    }
  };
}

export function DataCacheProvider({ children }: { children: React.ReactNode }) {
  const storeRef = useRef<DataCacheStore | null>(null);
  if (!storeRef.current) {
    storeRef.current = createDataCacheStore(
      () => typeof window === "undefined" ? null : window.localStorage.getItem("token")
    );
  }
  const store = storeRef.current;
  const hydratedRef = useRef(false);
  const [persistedHydrationRevision, setPersistedHydrationRevision] = useState(0);
  const revision = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);

  const hydrate = useCallback(() => {
    if (hydratedRef.current || typeof window === "undefined") return;
    hydratedRef.current = true;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (!stored) return;
      const entries = JSON.parse(stored) as [string, CacheEntry<unknown>][];
      store.hydrate(entries);
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, [store]);

  const persist = useCallback(() => {
    if (typeof window === "undefined") return;
    const entries = store.entries().slice(-80);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  }, [store]);

  useEffect(() => {
    hydrate();
    setPersistedHydrationRevision(1);
  }, [hydrate]);

  useEffect(() => subscribeEffectiveDataRefresh(() => {
    hydrate();
    invalidateEffectiveDataCaches(store);
    persist();
  }), [hydrate, persist, store]);

  useEffect(() => subscribeSessionChange(() => {
    hydratedRef.current = true;
    store.resetSession();
  }), [store]);

  useEffect(() => startEffectiveDataRevisionPolling({
    readRevision: api.effectiveDataRevision,
    onChanged: () => publishEffectiveDataRefresh(),
    getSessionKey: getToken
  }), []);

  const value = useMemo<DataCacheContextValue>(
    () => ({
      get: (key) => {
        return store.get(key);
      },
      set: (key, data) => {
        hydrate();
        store.set(key, data);
        persist();
      },
      getInflight: (key) => store.getInflight(key),
      setInflight: (key, promise) => store.setInflight(key, promise),
      clearInflight: (key, promise) => store.clearInflight(key, promise),
      getVersion: (key) => store.getVersion(key),
      getSessionRevision: () => store.getSessionRevision(),
      resetSession: () => store.resetSession(),
      invalidate: (prefix) => {
        hydrate();
        store.invalidate(prefix);
        persist();
      },
      invalidateMany: (prefixes) => {
        hydrate();
        store.invalidateMany(prefixes);
        persist();
      },
      revision: revision + persistedHydrationRevision,
      sessionRevision: store.getSessionRevision()
    }),
    [hydrate, persist, persistedHydrationRevision, revision, store]
  );

  return <DataCacheContext.Provider value={value}>{children}</DataCacheContext.Provider>;
}

export function useDataCache(): DataCacheContextValue {
  const context = useContext(DataCacheContext);
  if (!context) throw new Error("useDataCache must be used inside DataCacheProvider");
  return context;
}

export function useCachedQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: CachedQueryOptions = {}
): CachedQueryState<T> {
  const context = useDataCache();

  const enabled = options.enabled ?? true;
  const authenticated = typeof window === "undefined" || Boolean(getToken());
  const active = enabled && authenticated;
  const staleTimeMs = options.staleTimeMs ?? DEFAULT_STALE_TIME_MS;
  const cached = context.get<T>(key);
  const fetcherRef = useRef(fetcher);
  const [state, setState] = useState<KeyedCachedQueryState<T>>({
    key,
    sessionRevision: context.sessionRevision,
    data: cached?.data,
    error: null,
    isInitialLoading: active && !cached,
    isUpdating: false
  });

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    if (!active) {
      setState((current) => ({
        ...(current.key === key && current.sessionRevision === context.sessionRevision
          ? current
          : { key, sessionRevision: context.sessionRevision, data: context.get<T>(key)?.data, error: null }),
        key,
        sessionRevision: context.sessionRevision,
        isInitialLoading: false,
        isUpdating: false
      }));
      return;
    }

    let cancelled = false;
    const entry = context.get<T>(key);
    const hasFreshData = entry && Date.now() - entry.updatedAt < staleTimeMs;

    if (entry) {
      setState((current) => ({
        key,
        sessionRevision: context.sessionRevision,
        data: entry.data,
        error: null,
        isInitialLoading: false,
        isUpdating: !hasFreshData
      }));
    } else {
      setState((current) => ({
        key,
        sessionRevision: context.sessionRevision,
        data: current.key === key && current.sessionRevision === context.sessionRevision ? current.data : undefined,
        error: null,
        isInitialLoading: current.key !== key || current.sessionRevision !== context.sessionRevision || !current.data,
        isUpdating: current.key === key && current.sessionRevision === context.sessionRevision && Boolean(current.data)
      }));
    }

    if (hasFreshData) return;

    const existingRequest = context.getInflight<T>(key);
    const request = existingRequest ?? fetcherRef.current();
    const requestVersion = context.getVersion(key);
    if (!existingRequest) context.setInflight(key, request);

    request
      .then((data) => {
        if (context.getVersion(key) !== requestVersion) return;
        context.set(key, data);
        if (!cancelled) {
          setState({
            key,
            sessionRevision: context.sessionRevision,
            data,
            error: null,
            isInitialLoading: false,
            isUpdating: false
          });
        }
      })
      .catch((error: Error) => {
        if (context.getVersion(key) !== requestVersion) return;
        if (!cancelled) {
          setState((current) => ({
            ...(current.key === key && current.sessionRevision === context.sessionRevision
              ? current
              : { key, sessionRevision: context.sessionRevision, data: undefined }),
            key,
            sessionRevision: context.sessionRevision,
            error,
            isInitialLoading: false,
            isUpdating: false
          }));
        }
      })
      .finally(() => {
        if (!existingRequest) context.clearInflight(key, request);
      });

    return () => {
      cancelled = true;
    };
  }, [active, context, context.revision, context.sessionRevision, key, staleTimeMs]);

  const cachedIsFresh = cached && Date.now() - cached.updatedAt < staleTimeMs;
  const visibleState: CachedQueryState<T> = state.key === key && state.sessionRevision === context.sessionRevision
    ? state
    : {
        data: cached?.data,
        error: null,
        isInitialLoading: enabled && !cached,
        isUpdating: enabled && Boolean(cached) && !cachedIsFresh
      };
  return {
    data: visibleState.data,
    error: visibleState.error,
    isInitialLoading: visibleState.isInitialLoading,
    isUpdating: visibleState.isUpdating
  };
}
