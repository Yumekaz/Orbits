import { expect, test } from '@playwright/test';
import { clickNode, getDebug, loadFixture, openMenu, sampleCanvasPixels } from './helpers';

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


  test('runtime fixture switches edge source modes without blanking the canvas', async ({ page }) => {
    await loadFixture(page, 'runtime-graph.json');
    let debug = await getDebug(page);
    expect(debug.graphDynamicEdgeCount).toBe(2);
    expect(debug.edgeMode).toBe('combined');
    expect(debug.visibleEdgeCount).toBeGreaterThanOrEqual(3);

    await openMenu(page, 'btn-view');
    await page.locator('#edge-mode-runtime').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('runtime');
    debug = await getDebug(page);
    expect(debug.visibleEdgeCount).toBe(2);
    expect(debug.visibleDynamicEdgeCount).toBe(2);

    await page.locator('#edge-mode-static').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('static');
    debug = await getDebug(page);
    expect(debug.visibleEdgeCount).toBe(2);

    await page.locator('#edge-mode-combined').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('combined');
    await expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });

  test('node runtime fixture switches edge source modes without blanking the canvas', async ({ page }) => {
    await loadFixture(page, 'node-runtime-graph.json');
    let debug = await getDebug(page);
    expect(debug.graphDynamicEdgeCount).toBe(2);
    expect(debug.edgeMode).toBe('combined');
    expect(debug.visibleEdgeCount).toBeGreaterThanOrEqual(3);

    await openMenu(page, 'btn-view');
    await page.locator('#edge-mode-runtime').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('runtime');
    debug = await getDebug(page);
    expect(debug.visibleEdgeCount).toBe(2);
    expect(debug.visibleDynamicEdgeCount).toBe(2);

    await page.locator('#edge-mode-static').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('static');
    debug = await getDebug(page);
    expect(debug.visibleEdgeCount).toBe(2);

    await page.locator('#edge-mode-combined').click();
    await expect.poll(async () => (await getDebug(page)).edgeMode).toBe('combined');
    await expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });

  test('stale runtime fixture surfaces confidence context', async ({ page }) => {
    await loadFixture(page, 'runtime-stale-waste-graph.json');

    await expect(page.getByTestId('runtime-pill')).toBeVisible();
    await expect(page.getByTestId('runtime-pill')).toContainText('Runtime stale');
    await expect(page.getByTestId('runtime-pill')).toContainText('2 sessions');
    await expect(page.getByTestId('warning-banner')).toContainText('Runtime trace stale');

    await expect(page.locator('#waste-list')).toContainText('ORPHAN');
    await expect(page.locator('#waste-list')).toContainText('runtime stale');
    await page.locator('#waste-list .list-item').filter({ hasText: 'visualizer_worker.js' }).click();
    await expect(page.locator('#imeta')).toContainText('Confidence');
    await expect(page.locator('#imeta')).toContainText('stale');
    await expect(page.locator('#ihistory')).toContainText('Blame');

    await clickNode(page, 'runtime/generated.ts');
    await expect(page.locator('#imeta')).toContainText('Runtime-only node');
  });

  test('unsupported-language fixture shows and dismisses the warning banner', async ({ page }) => {
    await loadFixture(page, 'unsupported-lang-graph.json');
    await expect(page.getByTestId('warning-banner')).toHaveClass(/show/);
    await expect(page.getByTestId('warning-banner')).toContainText('parser unavailable');
    await page.getByTestId('warning-dismiss').click();
    await expect(page.getByTestId('warning-banner')).not.toHaveClass(/show/);
  });

  test('large graph fixture loads without crashing and reports a perf mode', async ({ page }) => {
    const fixture = await loadFixture(page, 'large-graph.json');
    const debug = await getDebug(page);
    expect(debug.graphNodeCount).toBe((fixture as any).nodes.length);
    expect(debug.visibleNodeCount).toBeGreaterThan(0);
    expect(debug.performanceState.perfMode).toBe('auto');
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
    await expect(page.getByTestId('health-pill')).toBeVisible();
  });
});
