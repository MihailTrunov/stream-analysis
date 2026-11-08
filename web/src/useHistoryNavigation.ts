import { useEffect, useRef, useState } from 'react';
import { type ReplayBar, type ReplaySource } from './replay';
import { localWindowEnd, navigationCoverage, prepareNavigation, publishNavigation, waitForHistory,
  type NavigationOptions, type NavigationPlan } from './historyNavigation';
import { readHistoryWindow } from './useHistoryWindow';
import { readProviderPrices, providerWindowEnd, type ProviderPriceWindow } from './providerPrices';

interface Callbacks {
  onPersist: (plan: NavigationPlan | undefined) => void;
  onReady: (source: ReplaySource, bars: ReplayBar[], target: string, end: string, intent: 'move' | 'replay', initialization?: string) => Promise<void> | void;
  onError: (message: string) => void;
  onPrices?: (prices: ProviderPriceWindow, target: string) => void;
}
export function useHistoryNavigation(active: boolean, initialPlan: NavigationPlan | undefined, callbacks: Callbacks) {
  const current = useRef(callbacks); current.current = callbacks;
  const plan = useRef(initialPlan);
  const lastOptions = useRef<NavigationOptions | undefined>(undefined);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const phase = useRef(initialPlan ? 'restored' : 'idle');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  function cancel(clear = true) {
    ++generation.current; controller.current?.abort(); controller.current = null;
    setBusy(false);
    if (clear) { plan.current = undefined; current.current.onPersist(undefined); setMessage(null); setFailed(false); phase.current = 'idle'; }
    else phase.current = 'paused';
  }
  async function run(options?: NavigationOptions) {
    cancel(false);
    if (options) { lastOptions.current = options; plan.current = undefined; current.current.onPersist(undefined); }
    const ownGeneration = generation.current;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setFailed(false); phase.current = 'loading';
    const valid = () => !abort.signal.aborted && ownGeneration === generation.current;
    // Start price acquisition before analytical calendar preparation can reject the period.
    const priceOptions = options && !options.source.non_research_grade && current.current.onPrices
      && (options.intent === 'replay' || Date.parse(options.target) < Date.parse(options.source.range_start)
        || Date.parse(options.target) >= Date.parse(options.source.range_end))
      ? { instrument: options.source.instrument_id, timeframe: options.timeframe, target: options.target }
      : undefined;
    const prices = priceOptions ? readProviderPrices(priceOptions.instrument, priceOptions.timeframe,
      providerWindowEnd(priceOptions.target, priceOptions.timeframe), abort.signal).catch(() => null) : null;
    try {
      if (options?.intent === 'move' && Date.parse(options.target) >= Date.parse(options.source.range_start)
        && Date.parse(options.target) < Date.parse(options.source.range_end)) {
        setMessage('Loading earlier chart candles…');
        const end = localWindowEnd(options.source, options.target, options.timeframe);
        const bars = await readHistoryWindow(options.source, end, options.timeframe);
        if (!valid()) return;
        setBusy(false);
        await current.current.onReady(options.source, bars, options.target, end, 'move');
      } else {
        if (options) {
          if (options.source.non_research_grade) throw new Error('This synthetic dataset has no provider history outside its retained period. Choose a provider dataset.');
          setMessage('Preparing historical data…');
          const next = await prepareNavigation(options, abort.signal);
          if (!valid()) return;
          plan.current = next; current.current.onPersist(next);
        }
        const pending = plan.current;
        if (!pending) return;
        let coverage = await navigationCoverage(pending, abort.signal);
        while (!coverage.ready) {
          if (!valid()) return;
          setMessage(`Waiting for historical data… ${coverage.completed} of ${pending.request_ids.length} ranges ready.`);
          await waitForHistory(abort.signal);
          coverage = await navigationCoverage(pending, abort.signal);
        }
        if (!valid()) return;
        setMessage('Preparing chart from retained history…');
        const source = await publishNavigation(pending, coverage.requests, abort.signal);
        const bars = await readHistoryWindow(source, pending.end, pending.timeframe);
        if (!valid()) return;
        plan.current = undefined; current.current.onPersist(undefined);
        setBusy(false);
        await current.current.onReady(source, bars, pending.target, pending.end, pending.intent, pending.initialization);
      }
      if (!valid()) return;
      plan.current = undefined; current.current.onPersist(undefined);
      phase.current = 'idle'; setMessage(null);
    } catch (error) {
      const preview = await prices;
      if (valid() && preview?.bars.length && priceOptions) {
        plan.current = undefined; current.current.onPersist(undefined); phase.current = 'idle';
        setFailed(false); setMessage(null);
        current.current.onPrices?.(preview, priceOptions.target);
      } else if (valid()) { phase.current = 'failed'; setFailed(true); setMessage('Chart navigation could not finish.'); current.current.onError(error instanceof Error ? error.message : String(error)); }
    } finally { if (valid()) { setBusy(false); controller.current = null; } }
  }
  const actions = useRef({ run, cancel }); actions.current = { run, cancel };
  useEffect(() => {
    if (!active) { actions.current.cancel(false); return; }
    if (plan.current && ['restored', 'paused'].includes(phase.current)) void actions.current.run();
    return () => { actions.current.cancel(false); };
  }, [active]);
  return { busy, message, failed, begin: run, cancel: () => cancel(), retry: () => run(plan.current ? undefined : lastOptions.current) };
}
