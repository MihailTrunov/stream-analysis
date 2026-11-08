import { useEffect, useRef, useState } from 'react';
import { readProviderPrices, type ProviderPriceWindow } from './providerPrices';
import { apiJson, type Timeframe } from './replay';

/** Acquisition stays server-side for connected hidden tabs; only the visible chart polls. */
export function useProviderPrices(instrument: string, timeframe: Timeframe, active: boolean,
  enabled: boolean, end?: string, monitor = true) {
  const [registered, setRegistered] = useState(false);
  const [everActive, setEverActive] = useState(active);
  const [visible, setVisible] = useState(!document.hidden);
  const [window, setWindow] = useState<{ key: string; value: ProviderPriceWindow } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = `${instrument}:${timeframe}:${end ?? 'latest'}`;
  useEffect(() => { if (active) setEverActive(true); }, [active]);
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    if (!everActive || !enabled || !monitor) return;
    let disposed = false;
    // Each effect owns its ID: a delayed cleanup cannot disconnect a newer registration.
    const id = crypto.randomUUID();
    const remove = () => { void apiJson(`/live/price-subscriptions/${id}`, { method: 'DELETE' }).catch(() => undefined); };
    void apiJson('/live/price-subscriptions', { method: 'POST', body: JSON.stringify({
      subscription_id: id, instrument_id: instrument,
    }) }).then(() => { if (disposed) remove(); else setRegistered(true); })
      .catch(() => undefined); // A direct price read still runs if subscription setup fails.
    return () => { disposed = true; setRegistered(false); remove(); };
  }, [instrument, enabled, everActive, monitor]);
  useEffect(() => {
    if (!active || !visible || !enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await readProviderPrices(instrument, timeframe, end, controller.signal);
        if (!controller.signal.aborted) { setWindow({ key, value }); setError(null); }
      } catch (reason) { if (!controller.signal.aborted) setError(String(reason)); }
      if (!controller.signal.aborted && !end && monitor) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [instrument, timeframe, end, key, active, visible, enabled, monitor]);
  return { window: window?.key === key ? window.value : null, error, registered };
}

export function useProviderNavigation(instrument: string, timeframe: Timeframe, active: boolean,
  onError: (message: string) => void) {
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  function cancel() { controller.current?.abort(); controller.current = null; setBusy(false); }
  useEffect(() => () => { controller.current?.abort(); }, [instrument, timeframe, active]);
  async function load(end: string) {
    cancel();
    const own = new AbortController(); controller.current = own; setBusy(true);
    try {
      const result = await readProviderPrices(instrument, timeframe, end, own.signal);
      return own.signal.aborted ? null : result;
    } catch (reason) { if (!own.signal.aborted) onError(String(reason)); return null; }
    finally { if (controller.current === own) { controller.current = null; setBusy(false); } }
  }
  return { load, busy, cancel };
}
