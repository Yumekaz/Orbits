import { expect, test } from '@playwright/test';
import { clickNode, loadFixture, settleVisualFrame } from './helpers';

test.describe('@visual screenshot baselines', () => {
  test('default small graph', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await settleVisualFrame(page);
    await expect(page.locator('#app')).toHaveScreenshot('default-small-graph.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
    });
  });

  test('selected node', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await clickNode(page, 'app/main.py');
    await settleVisualFrame(page);
    await expect(page.locator('#app')).toHaveScreenshot('selected-node.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
    });
  });

  test('search active', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await page.getByTestId('btn-search').click();
    await page.getByTestId('search-input').fill('util');
    await settleVisualFrame(page);
    await expect(page.locator('#app')).toHaveScreenshot('search-active.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
    });
  });

  test('collapsed left rail', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await page.getByTestId('btn-waste').click();
    await settleVisualFrame(page);
    await expect(page.locator('#app')).toHaveScreenshot('collapsed-left-rail.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
    });
  });

  test('cycles panel open', async ({ page }) => {
    await loadFixture(page, 'cycles-graph.json');
    await page.getByTestId('btn-cycles').click();
    await settleVisualFrame(page);
    await expect(page.locator('#app')).toHaveScreenshot('cycles-panel-open.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
    });
  });
});
