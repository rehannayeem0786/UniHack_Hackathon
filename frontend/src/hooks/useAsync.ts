import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export interface AsyncState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
}

function message(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/**
 * Run an async function on mount (and on demand).
 *
 * Deliberately small: this app has a handful of endpoints and no cache
 * invalidation to speak of, so a query library would be more machinery than the
 * problem needs. What it does guarantee is that a response from a stale request
 * can never overwrite a newer one.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  { immediate = true }: { immediate?: boolean } = {},
): AsyncState<T> & { run: () => Promise<T | undefined>; reset: () => void } {
  const [state, setState] = useState<AsyncState<T>>({
    data: undefined,
    error: undefined,
    loading: immediate,
  });

  const mounted = useRef(true);
  const generation = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(async () => {
    const ticket = ++generation.current;
    setState((prev) => ({ ...prev, loading: true, error: undefined }));
    try {
      const data = await fnRef.current();
      if (!mounted.current || ticket !== generation.current) return undefined;
      setState({ data, error: undefined, loading: false });
      return data;
    } catch (error) {
      if (!mounted.current || ticket !== generation.current) return undefined;
      setState({ data: undefined, error: message(error), loading: false });
      return undefined;
    }
  }, []);

  const reset = useCallback(() => {
    generation.current++;
    setState({ data: undefined, error: undefined, loading: false });
  }, []);

  useEffect(() => {
    if (immediate) void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ...state, run, reset };
}

/**
 * Poll `fn` while `active` stays true. Used to follow a running job.
 *
 * `key` identifies what is being polled. Without it the hook holds the previous
 * subject's last value, and a caller that reacts to "status is done" will act on
 * a finished job while a freshly started one is still queued.
 */
export function usePoll<T>(
  fn: () => Promise<T>,
  active: boolean,
  { intervalMs = 700, key }: { intervalMs?: number; key?: string } = {},
): T | undefined {
  // The value is stored with the key it came from. Polling normally stops once
  // the subject reaches a terminal state, and the caller still needs to render
  // that result — so the value survives deactivation but never survives a
  // change of subject.
  const [entry, setEntry] = useState<{ key: string | undefined; value: T | undefined }>({
    key,
    value: undefined,
  });

  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    setEntry((prev) => (prev.key === key ? prev : { key, value: undefined }));
  }, [key]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const next = await fnRef.current();
        if (!cancelled) setEntry({ key, value: next });
      } catch {
        /* a dropped poll is not worth surfacing; the next one will report */
      }
    };

    void tick();
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, intervalMs, key]);

  return entry.key === key ? entry.value : undefined;
}
