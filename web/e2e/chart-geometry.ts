import type { Page } from '@playwright/test';
import type { IChartApi } from 'lightweight-charts';

/** Inspect the public chart API through its React ref without a production debug interface. */
export function chartGeometry(page: Page, visible?: { from: number; to: number }) {
  return page.locator('.trading-chart').evaluate((element, requested) => {
    interface Hook { memoizedState: { current?: unknown }; next: Hook | null }
    interface Fiber { memoizedProps?: { overlays?: unknown }; memoizedState: Hook; return: Fiber | null }
    const key = Object.keys(element).find(name => name.startsWith('__reactFiber'));
    if (!key) throw new Error('Chart React ref is unavailable.');
    let fiber = (element as unknown as Record<string, Fiber>)[key];
    while (fiber && !fiber.memoizedProps?.overlays) fiber = fiber.return!;
    for (let hook: Hook | null = fiber?.memoizedState ?? null; hook; hook = hook.next) {
      const chart = hook.memoizedState?.current as IChartApi | undefined;
      if (typeof chart?.timeScale !== 'function' || typeof chart?.priceScale !== 'function') continue;
      if (requested) chart.timeScale().setVisibleLogicalRange(requested);
      return { logical: chart.timeScale().getVisibleLogicalRange(), price: chart.priceScale('right').getVisibleRange(),
        autoScale: chart.priceScale('right').options().autoScale };
    }
    throw new Error('Chart API is unavailable.');
  }, visible);
}
