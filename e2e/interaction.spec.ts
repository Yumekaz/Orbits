import { expect, test } from '@playwright/test';
import { dragNode, getDebug, getNodeCanvasPoint, loadFixture, panCanvas, sampleCanvasPixels, wheelCanvas } from './helpers';

const DRAG_NODE = 'app/main.py';

test.describe('graph interactions', () => {
  test.beforeEach(async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
  });

  test('panning changes the zoom transform', async ({ page }) => {
    const canvas = page.getByTestId('graph-canvas');
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    const before = (await getDebug(page)).zoom;
    await panCanvas(page, { x: (box?.width ?? 800) - 80, y: (box?.height ?? 600) - 80 }, { x: (box?.width ?? 800) - 180, y: (box?.height ?? 600) - 80 });
    await expect.poll(async () => (await getDebug(page)).zoom.x).not.toBe(before.x);
  });

  test('wheel zoom changes scale', async ({ page }) => {
    const canvas = page.getByTestId('graph-canvas');
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    const before = (await getDebug(page)).zoom.k;
    await wheelCanvas(page, { x: (box?.width ?? 800) / 2, y: (box?.height ?? 600) / 2 }, -700);
    await expect.poll(async () => (await getDebug(page)).zoom.k).not.toBe(before);
  });

  test('dragging a node moves it without blanking the canvas', async ({ page }) => {
    const before = await getNodeCanvasPoint(page, DRAG_NODE);
    await dragNode(page, DRAG_NODE, 90, 40);
    await expect.poll(async () => (await getNodeCanvasPoint(page, DRAG_NODE)).x).toBeGreaterThan(before.x + 20);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });

  test('panel collapse and expand preserves rendered content', async ({ page }) => {
    await page.getByTestId('btn-waste').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(true);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);

    await page.getByTestId('btn-inspector').click();
    await expect.poll(async () => (await getDebug(page)).shellState.inspectorCollapsed).toBe(true);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);

    await page.getByTestId('btn-inspector').click();
    await page.getByTestId('btn-cycles').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(false);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });
});
