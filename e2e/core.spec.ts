import { expect, test } from '@playwright/test';
import { clickNode, getDebug, loadFixture, openMenu, sampleCanvasPixels, waitForGraphLoaded } from './helpers';

const SMALL_ENTRY = 'app/main.py';
const SMALL_SEARCH_TARGET = 'lib/util.py';

test.describe('core visualizer behavior', () => {
  test.beforeEach(async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
  });

  test('loads graph and renders non-empty canvas', async ({ page }) => {
    const debug = await waitForGraphLoaded(page, 5);
    expect(debug.graphLoaded).toBe(true);
    expect(debug.visibleNodeCount).toBeGreaterThan(0);
    expect(await sampleCanvasPixels(page)).toBeGreaterThan(20);
  });

  test('stats bar shows correct counts', async ({ page }) => {
    await expect(page.locator('#s-files')).toHaveText('5');
    await expect(page.locator('#s-edges')).toHaveText('4');
    await expect(page.locator('#s-orphans')).toHaveText('1');
  });

  test('canvas and minimap are visible', async ({ page }) => {
    await expect(page.getByTestId('graph-canvas')).toBeVisible();
    await expect(page.getByTestId('minimap')).toBeVisible();
    const canvasBox = await page.getByTestId('graph-canvas').boundingBox();
    const minimapBox = await page.getByTestId('minimap').boundingBox();
    expect(canvasBox?.width ?? 0).toBeGreaterThan(400);
    expect(minimapBox?.width ?? 0).toBeGreaterThan(100);
  });

  test('selecting a node updates the inspector', async ({ page }) => {
    await clickNode(page, SMALL_ENTRY);
    await expect(page.locator('#iname')).toContainText(SMALL_ENTRY);
    const debug = await getDebug(page);
    expect(debug.selectedNodeId).toBe(SMALL_ENTRY);
  });

  test('waste, cycles, and inspector toggles update shell state', async ({ page }) => {
    await page.getByTestId('btn-waste').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(true);

    await page.getByTestId('btn-cycles').click();
    await expect.poll(async () => (await getDebug(page)).shellState.leftRailCollapsed).toBe(false);
    await expect(page.locator('#cycles-panel')).not.toHaveClass(/left-panel-hidden/);

    await page.getByTestId('btn-inspector').click();
    await expect.poll(async () => (await getDebug(page)).shellState.inspectorCollapsed).toBe(true);
  });

  test('view, filter, and languages menus open and close', async ({ page }) => {
    await openMenu(page, 'btn-view');
    await expect.poll(async () => (await getDebug(page)).menuStates.layoutOpen).toBe(true);
    await page.mouse.click(20, 20);
    await expect.poll(async () => (await getDebug(page)).menuStates.layoutOpen).toBe(false);

    await openMenu(page, 'btn-filter');
    await expect.poll(async () => (await getDebug(page)).menuStates.filterOpen).toBe(true);
    await page.mouse.click(20, 20);
    await expect.poll(async () => (await getDebug(page)).menuStates.filterOpen).toBe(false);

    await openMenu(page, 'btn-languages');
    await expect.poll(async () => (await getDebug(page)).menuStates.langOpen).toBe(true);
    await page.mouse.click(20, 20);
    await expect.poll(async () => (await getDebug(page)).menuStates.langOpen).toBe(false);
  });

  test('search opens with slash and ctrl+k', async ({ page }) => {
    await page.keyboard.press('/');
    await expect(page.getByTestId('search-box')).toHaveClass(/open/);
    await page.keyboard.press('Escape');
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+K' : 'Control+K');
    await expect(page.getByTestId('search-box')).toHaveClass(/open/);
  });

  test('enter focuses the first search result', async ({ page }) => {
    await page.getByTestId('btn-search').click();
    await page.getByTestId('search-input').fill('util');
    await expect(page.locator('#search-status')).toContainText('match');
    await page.getByTestId('search-input').press('Enter');
    await expect.poll(async () => (await getDebug(page)).selectedNodeId).toBe(SMALL_SEARCH_TARGET);
  });

  test('escape clears search and closes the search box', async ({ page }) => {
    await page.getByTestId('btn-search').click();
    await page.getByTestId('search-input').fill('core');
    await page.getByTestId('search-input').press('Escape');
    await expect(page.getByTestId('search-input')).toHaveValue('');
    await expect.poll(async () => (await getDebug(page)).menuStates.searchOpen).toBe(false);
  });
});
