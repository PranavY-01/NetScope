import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever `deps` change; refetch every `pollMs` while `poll(data)` is true. */
export function useResource<T>(
  load: () => Promise<T>,
  deps: unknown[],
  options: { pollMs?: number; poll?: (data: T | null) => boolean } = {},
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const pollRef = useRef(options.poll);
  pollRef.current = options.poll;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const result = await loadRef.current();
        if (cancelled) return;
        setData(result);
        setError(null);
        if (options.pollMs && pollRef.current?.(result)) {
          timer = setTimeout(run, options.pollMs);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, options.pollMs]);

  return { data, error, loading, reload };
}
