"use client";

import { useEffect, useState } from "react";

import { ApiError, disconnectZoho, fetchZohoStatus, startZohoConnect, type ZohoStatus } from "@/lib/api";
import { cleanedUrl, formatConnectedAt, readZohoReturn, type ZohoReturn } from "@/lib/zoho";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

type Busy = "connecting" | "disconnecting" | null;

function goTo(url: string) {
  window.location.assign(url);
}

const primary =
  "rounded bg-foreground px-4 py-1.5 text-sm font-medium text-background disabled:cursor-not-allowed disabled:opacity-40";
const danger =
  "rounded border border-red-500 px-4 py-1.5 text-sm font-medium text-red-600 disabled:cursor-not-allowed disabled:opacity-40";
const quiet =
  "rounded border border-zinc-300 px-4 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700";

export default function ZohoPanel({ navigate = goTo }: { navigate?: (url: string) => void }) {
  const [status, setStatus] = useState<ZohoStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [returned, setReturned] = useState<ZohoReturn | null>(() =>
    typeof window === "undefined" ? null : readZohoReturn(window.location.search),
  );
  const [busy, setBusy] = useState<Busy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (returned) window.history.replaceState(null, "", cleanedUrl(window.location.href));
  }, [returned]);

  useEffect(() => {
    if (!API_URL) return;
    let cancelled = false;
    fetchZohoStatus(API_URL)
      .then((fresh) => {
        if (cancelled) return;
        setStatus(fresh);
        setStatusError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setStatusError(err instanceof ApiError ? err.message : "Something went wrong.");
      });
    return () => {
      cancelled = true;
    };
  }, [version]);

  if (!API_URL) return null;
  const apiUrl = API_URL;

  async function connect() {
    setBusy("connecting");
    setActionError(null);
    setNotice(null);
    setReturned(null);
    try {
      navigate(await startZohoConnect(apiUrl));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    setBusy("disconnecting");
    setActionError(null);
    setNotice(null);
    try {
      const result = await disconnectZoho(apiUrl);
      setConfirming(false);
      setNotice(
        result.revoked || !result.was_connected
          ? "Zoho is disconnected."
          : "Zoho is disconnected here, but Zoho could not be told to cancel the access. You can also remove DocFlow under Connected Apps in your Zoho account.",
      );
      setVersion((current) => current + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  const connectedAt = formatConnectedAt(status?.connected_at ?? null);
  const working = busy !== null;

  return (
    <section aria-label="Zoho Books" className="mt-6 flex max-w-3xl flex-col gap-3">
      {returned?.kind === "connected" && (
        <p role="status" className="flex flex-wrap items-center gap-3 rounded border border-green-600 p-3 text-sm text-green-600">
          Zoho is connected.
          <button type="button" onClick={() => setReturned(null)} className="underline">
            Dismiss
          </button>
        </p>
      )}
      {returned?.kind === "error" && (
        <p role="alert" className="flex flex-wrap items-center gap-3 rounded border border-red-500 p-3 text-sm text-red-600">
          {returned.message}
          <button type="button" onClick={() => setReturned(null)} className="underline">
            Dismiss
          </button>
        </p>
      )}
      {notice && (
        <p role="status" className="rounded border border-zinc-300 p-3 text-sm dark:border-zinc-700">
          {notice}
        </p>
      )}
      {actionError && (
        <p role="alert" className="rounded border border-red-500 p-3 text-sm text-red-600">
          {actionError}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-zinc-300 p-3 dark:border-zinc-700">
        <h2 className="font-semibold">Zoho Books</h2>

        {!status && !statusError && <span className="text-sm text-zinc-600 dark:text-zinc-400">Checking…</span>}

        {statusError && (
          <>
            <span className="text-sm text-amber-600">Could not check Zoho: {statusError}</span>
            <button type="button" onClick={() => setVersion((current) => current + 1)} className={quiet}>
              Retry
            </button>
          </>
        )}

        {status && !status.configured && (
          <span className="text-sm text-zinc-600 dark:text-zinc-400">Zoho is not set up on this server.</span>
        )}

        {status?.configured && status.connected && (
          <>
            <span className="rounded-full border border-green-600 px-3 py-0.5 text-sm font-medium text-green-600">
              Connected
            </span>
            <span className="text-sm text-zinc-600 dark:text-zinc-400">
              by {status.connected_by}
              {connectedAt ? ` · ${connectedAt}` : ""}
            </span>
          </>
        )}

        {status?.configured && !status.connected && (
          <>
            <span className="rounded-full border border-zinc-400 px-3 py-0.5 text-sm font-medium dark:border-zinc-600">
              Not connected
            </span>
            {status.message && <span className="text-sm text-amber-600">{status.message}</span>}
          </>
        )}

        <div className="ml-auto flex flex-wrap gap-2">
          {status?.configured && !status.connected && (
            <button type="button" onClick={connect} disabled={working} className={primary}>
              {busy === "connecting" ? "Opening Zoho…" : "Connect Zoho"}
            </button>
          )}
          {status?.configured && status.connected && !confirming && (
            <button type="button" onClick={() => setConfirming(true)} disabled={working} className={quiet}>
              Disconnect
            </button>
          )}
        </div>
      </div>

      {confirming && (
        <div className="flex flex-col gap-3 rounded border border-red-500 p-3">
          <p className="text-sm">
            Disconnect Zoho? DocFlow will lose access to your Zoho Books account until you connect again.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={disconnect} disabled={working} className={danger}>
              {busy === "disconnecting" ? "Disconnecting…" : "Yes, disconnect"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={working} className={quiet}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}