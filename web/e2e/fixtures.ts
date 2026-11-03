import { test as base, expect } from '@playwright/test';

export const test = base.extend<{ replayCleanup: void }>({
  replayCleanup: [async ({ request }, use) => {
    await use();
    const snapshot = await (await request.get('/api/replay/active')).json() as { active: { run_id: string } | null };
    if (snapshot.active) await request.post(`/api/replay/${snapshot.active.run_id}/stop`);
  }, { auto: true }],
});
export { expect };
