"use client";

import { useState, useSyncExternalStore } from "react";

import { ApiError, checkAccessKey } from "@/lib/api";
import { clearAccessKey, getAccessKey, getNotice, saveAccessKey, subscribe } from "@/lib/session";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

const neverChanges = () => () => {};

export default function AccessGate({ children }: { children: React.ReactNode }) {
  const ready = useSyncExternalStore(neverChanges, () => true, () => false);
  const key = useSyncExternalStore(subscribe, getAccessKey, () => null);
  const notice = useSyncExternalStore(subscribe, getNotice, () => null);

  const [passcode, setPasscode] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!API_URL) return <>{children}</>;
  if (!ready) return null;

  if (key) {
    return (
      <>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => clearAccessKey()}
            className="text-sm text-zinc-600 underline dark:text-zinc-400"
          >
            Log out
          </button>
        </div>
        {children}
      </>
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = passcode.trim();
    if (!candidate) return;
    setChecking(true);
    setError(null);
    try {
      if (await checkAccessKey(candidate, API_URL!)) {
        saveAccessKey(candidate);
        setPasscode("");
      } else {
        setError("That passcode is not correct.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mt-8 flex max-w-md flex-col gap-4">
      <h2 className="text-xl font-semibold">Enter the passcode</h2>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        This demo is private. The passcode was shared with you along with the link.
      </p>
      {notice && (
        <p role="alert" className="rounded border border-amber-500 p-3 text-sm text-amber-600">
          {notice}
        </p>
      )}
      <label htmlFor="passcode" className="font-medium">
        Passcode
      </label>
      <input
        id="passcode"
        name="passcode"
        type="password"
        autoComplete="off"
        value={passcode}
        onChange={(event) => setPasscode(event.target.value)}
        disabled={checking}
        className="rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
      />
      {error && (
        <p role="alert" className="rounded border border-red-500 p-3 text-sm text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={!passcode.trim() || checking}
        className="w-fit rounded bg-foreground px-5 py-2 font-medium text-background disabled:cursor-not-allowed disabled:opacity-40"
      >
        {checking ? "Checking…" : "Unlock"}
      </button>
    </form>
  );
}