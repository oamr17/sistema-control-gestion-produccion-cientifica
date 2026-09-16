"use client";

import { useEffect, useRef, useState } from "react";

import { createAuthenticatedBlobUrlOwner } from "@/lib/api";
import { subscribeSessionChange } from "@/lib/auth";

export function AuthenticatedPdfButton({
  load,
  children,
  className
}: {
  load: (options: RequestInit) => Promise<Blob>;
  children: React.ReactNode;
  className?: string;
}) {
  const ownerRef = useRef<ReturnType<typeof createAuthenticatedBlobUrlOwner> | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);

  if (!ownerRef.current) ownerRef.current = createAuthenticatedBlobUrlOwner();

  useEffect(() => {
    if (!ownerRef.current) ownerRef.current = createAuthenticatedBlobUrlOwner();
    const clearOwnedDownload = () => {
      requestRef.current?.abort();
      requestRef.current = null;
      ownerRef.current?.dispose();
      ownerRef.current = createAuthenticatedBlobUrlOwner();
      objectUrlRef.current = null;
      setIsLoading(false);
      setHasError(false);
    };
    const unsubscribe = subscribeSessionChange(clearOwnedDownload);
    return () => {
      unsubscribe();
      requestRef.current?.abort();
      requestRef.current = null;
      ownerRef.current?.dispose();
      ownerRef.current = null;
      objectUrlRef.current = null;
    };
  }, []);

  const open = async () => {
    requestRef.current?.abort();
    const previewWindow = window.open("about:blank", "_blank");
    if (!previewWindow) {
      setHasError(true);
      return;
    }
    previewWindow.opener = null;
    const controller = new AbortController();
    let previewLoaded = false;
    let nextUrl: string | null = null;
    requestRef.current = controller;
    setIsLoading(true);
    setHasError(false);
    try {
      nextUrl = (await ownerRef.current?.replace(
        objectUrlRef.current,
        () => load({ signal: controller.signal })
      )) ?? null;
      if (!nextUrl || controller.signal.aborted) return;
      if (previewWindow.closed) throw new Error("PDF preview window unavailable");
      previewWindow.location.replace(nextUrl);
      objectUrlRef.current = nextUrl;
      previewLoaded = true;
    } catch {
      if (nextUrl) {
        ownerRef.current?.revoke(nextUrl);
        objectUrlRef.current = null;
      }
      if (!controller.signal.aborted) setHasError(true);
    } finally {
      if (!previewLoaded) previewWindow?.close();
      if (requestRef.current === controller) {
        requestRef.current = null;
        setIsLoading(false);
      }
    }
  };

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button type="button" onClick={() => void open()} disabled={isLoading} className={className}>
        {isLoading ? "Cargando PDF..." : children}
      </button>
      {hasError ? <span role="status" className="text-xs text-coral">No se pudo abrir el PDF.</span> : null}
    </span>
  );
}
