import { test } from '@playwright/test';
import { clickNode, loadFixture } from './helpers';

test.describe.skip('stage 2 visual baselines (deferred)', () => {
  test('default small graph', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await test.info().attach('note', { body: 'Deferred until Stage 1 is stable.', contentType: 'text/plain' });
  });

  test('selected node', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await clickNode(page, 'app/main.py');
  });

  test('search active', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await page.getByTestId('btn-search').click();
    await page.getByTestId('search-input').fill('util');
  });

  test('collapsed left rail', async ({ page }) => {
    await loadFixture(page, 'small-graph.json');
    await page.getByTestId('btn-waste').click();
  });

  test('cycles panel open', async ({ page }) => {
    await loadFixture(page, 'cycles-graph.json');
    await page.getByTestId('btn-cycles').click();
  });
});
