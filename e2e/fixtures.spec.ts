import { expect, test } from '@playwright/test';
import { getDebug, loadFixture, sampleCanvasPixels } from './helpers';

test.describe('fixture-specific behavior', () => {
  test('cycles fixture exposes cycle panel interactions', async ({ page }) => {
    await loadFixture(page, 'cycles-graph.json');
    await page.getByTestId('btn-cycles').click();
    await expect(page.locator('#cycle-list')).toContainText('Cycle 1');
    await page.locator('#cycle-list .cycle-node').first().click();
    await expect.poll(async () => (await getDebug(page)).selectedNodeId).toBe('src/a.py');
  });

  test('mixed-language fixture filters visible nodes by language', async ({ page }) => {
    await loadFixture(page, 'mixed-lang-graph.json');
    const before = (await getDebug(page)).visibleNodeCount;
    await page.getByTestId('btn-languages').click();
    await page.locator('#lang-chip-grid').getByRole('button', { name: /Go/i }).click();
    await expect.poll(async () => (await getDebug(page)).visibleNodeCount).toBeLessThan(before);
  });

  test('unsupported-language fixture shows and dismisses the warning banner', async ({ page }) => {
    await loadFixture(page, 'unsupported-lang-graph.json');
    await expect(page.getByTestId('warning-banner')).toHaveClass(/show/);
    await expect(page.getByTestId('warning-banner')).toContainText('parser unavailable');
    await page.getByTestId('warning-dismiss').click();
    await expect(page.getByTestId('warning-banner')).not.toHaveClass(/show/);
  });

  test('large graph fixture loads without crashing and enters a degraded perf state', async ({ page }) => {
    const fixture = await loadFixture(page, 'large-graph.json');
    const debug = await getDebug(page);
    expect(debug.graphNodeCount).toBe((fixture as any).nodes.length);
    expect(debug.visibleNodeCount).toBeGreaterThan(0);
    expect(debug.performanceState.motionEnabled).toBe(false);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
    await expect(page.getByTestId('health-pill')).toBeVisible();
  });
});
