const STORAGE_KEY = "docflow.accessKey";

let memoryKey: string | null = null;
let notice: string | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getAccessKey(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) ?? memoryKey;
  } catch {
    return memoryKey;
  }
}

export function getNotice(): string | null {
  return notice;
}

export function saveAccessKey(key: string): void {
  memoryKey = key;
  notice = null;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, key);
  } catch {
    // Storage can be blocked; the key then lasts until the page is reloaded.
  }
  emit();
}

export function clearAccessKey(message?: string): void {
  memoryKey = null;
  notice = message ?? null;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing stored, nothing to remove.
  }
  emit();
}