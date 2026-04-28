import { expect, test } from '@playwright/test';
import { getDebug, loadFixture, panCanvas, sampleCanvasPixels } from './helpers';

test.describe('visualizer regressions', () => {
  test.beforeEach(async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
  });

  test('waste fully collapses the left rail', async ({ page }) => {
    await page.getByTestId('btn-waste').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(true);
    const box = await page.getByTestId('left-rail').boundingBox();
    expect(box?.width ?? 0).toBeLessThanOrEqual(2);
  });

  test('no pan wall remains after toggling the left rail', async ({ page }) => {
    const canvas = page.getByTestId('graph-canvas');
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();

    await page.getByTestId('btn-waste').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(true);
    const beforeCollapsed = (await getDebug(page)).zoom.x;
    await panCanvas(page, { x: (box?.width ?? 900) - 70, y: 180 }, { x: (box?.width ?? 900) - 220, y: 180 });
    await expect.poll(async () => (await getDebug(page)).zoom.x).not.toBe(beforeCollapsed);

    await page.getByTestId('btn-cycles').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(false);
    const beforeExpanded = (await getDebug(page)).zoom.x;
    await panCanvas(page, { x: (box?.width ?? 900) - 70, y: 220 }, { x: (box?.width ?? 900) - 220, y: 220 });
    await expect.poll(async () => (await getDebug(page)).zoom.x).not.toBe(beforeExpanded);
  });

  test('graph still renders after viewport resize', async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 840 });
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
    await page.setViewportSize({ width: 1100, height: 720 });
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });

  test('left rail panels scroll when lists are long', async ({ page }) => {
    const nodes = Array.from({ length: 70 }, (_, index) => ({
      id: `legacy/dead-${index}.py`,
      name: `dead-${index}.py`,
      filepath: `legacy/dead-${index}.py`,
      language: 'python',
      classification: 'ORPHAN',
      size: 20,
      depth: -1,
    }));
    await page.evaluate((payload) => (window as any).loadGraph(payload), {
      nodes,
      edges: [],
      cycles: [],
      islands: [],
      waste: nodes.map((node) => ({ id: node.id, classification: 'ORPHAN', size: node.size, island_id: -1 })),
      summary: { health_score: 10, cycle_count: 0, island_count: 0, counts: { ORPHAN: nodes.length } },
      meta: { total_files: nodes.length, total_edges: 0, import_stats: {}, languages: ['python'] },
    });
    await page.locator('[data-left-panel="waste-panel"]').click();

    const result = await page.locator('#waste-list').evaluate((element) => {
      element.scrollTop = element.scrollHeight;
      return {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        scrollTop: element.scrollTop,
      };
    });

    expect(result.scrollHeight).toBeGreaterThan(result.clientHeight);
    expect(result.scrollTop).toBeGreaterThan(0);
  });

  test('graph still renders after opening and closing popover menus', async ({ page }) => {
    await page.getByTestId('btn-view').click();
    await expect.poll(async () => (await getDebug(page)).menuStates.layoutOpen).toBe(true);
    await page.mouse.click(20, 20);

    await page.getByTestId('btn-filter').click();
    await expect.poll(async () => (await getDebug(page)).menuStates.filterOpen).toBe(true);
    await page.mouse.click(20, 20);

    await page.getByTestId('btn-languages').click();
    await expect.poll(async () => (await getDebug(page)).menuStates.langOpen).toBe(true);
    await page.mouse.click(20, 20);

    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });
});
