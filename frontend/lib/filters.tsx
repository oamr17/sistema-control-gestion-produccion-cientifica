"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { getProfile } from "@/lib/auth";
import type { Career, Period } from "@/lib/types";

type FilterContextValue = {
  careers: Career[];
  periods: Period[];
  yearLabel: string;
  cycle: number;
  careerId: string;
  role: string | null;
  effectiveCareerId: string;
  setYearLabel: (value: string) => void;
  setCycle: (value: number) => void;
  setCareerId: (value: string) => void;
};

const FilterContext = createContext<FilterContextValue | null>(null);

const STORAGE_KEY = "global_filters_v3";
const METADATA_STORAGE_KEY = "global_metadata_v2";

export function FilterProvider({ children }: { children: React.ReactNode }) {
  const [profile, setProfile] = useState<ReturnType<typeof getProfile>>(null);
  const [careers, setCareers] = useState<Career[]>([]);
  const [periods, setPeriods] = useState<Period[]>([]);
  const [yearLabel, setYearLabel] = useState("2025-2026");
  const [cycle, setCycle] = useState(1);
  const [careerId, setCareerId] = useState("");

  useEffect(() => {
    setProfile(getProfile());
  }, []);

  useEffect(() => {
    const storedMetadata = window.localStorage.getItem(METADATA_STORAGE_KEY);
    if (storedMetadata) {
      try {
        const parsed = JSON.parse(storedMetadata) as { careers?: Career[]; periods?: Period[] };
        if (parsed.careers?.length) setCareers(parsed.careers);
        if (parsed.periods?.length) setPeriods(parsed.periods);
      } catch {
        window.localStorage.removeItem(METADATA_STORAGE_KEY);
      }
    }
  }, []);

  useEffect(() => {
    const activeProfile = getProfile();
    if (!activeProfile) {
      return;
    }

    Promise.all([api.careers(), api.periods()])
      .then(([careerData, periodData]) => {
        setCareers(careerData);
        setPeriods(periodData);
        window.localStorage.setItem(
          METADATA_STORAGE_KEY,
          JSON.stringify({ careers: careerData, periods: periodData })
        );
      })
      .catch(() => {
        // Keep previous metadata visible if a background refresh fails.
      });
  }, [profile?.role, profile?.career_id]);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return;
    try {
      const parsed = JSON.parse(stored) as {
        yearLabel?: string;
        cycle?: number;
        careerId?: string;
      };
      if (parsed.yearLabel) setYearLabel(parsed.yearLabel);
      if (parsed.cycle) setCycle(parsed.cycle);
      if (parsed.careerId) setCareerId(parsed.careerId);
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ yearLabel, cycle, careerId })
    );
  }, [yearLabel, cycle, careerId]);

  const effectiveCareerId =
    profile?.role === "CAREER_MANAGER" ? String(profile.career_id ?? "") : careerId;

  const value = useMemo(
    () => ({
      careers,
      periods,
      yearLabel,
      cycle,
      careerId,
      role: profile?.role ?? null,
      effectiveCareerId,
      setYearLabel,
      setCycle,
      setCareerId
    }),
    [careers, periods, yearLabel, cycle, careerId, profile?.role, effectiveCareerId]
  );

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>;
}

export function useGlobalFilters() {
  const context = useContext(FilterContext);
  if (!context) {
    throw new Error("useGlobalFilters must be used inside FilterProvider");
  }
  return context;
}
