import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { setTimeout as delay } from 'node:timers/promises';

import { chromium } from '@playwright/test';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';

const ROOT = process.cwd();
const BASE_URL = 'http://127.0.0.1:8766';
const VISUALIZER_URL = `${BASE_URL}/visualizer.html`;
const FIXTURES_DIR = path.join(ROOT, 'e2e', 'fixtures');
const SNAPSHOT_DIR = path.join(ROOT, 'e2e', 'visual.spec.ts-snapshots');
const DIFF_DIR = path.join(ROOT, 'test-results', 'visual-diffs');
const UPDATE = process.argv.includes('--update');
const MAX_DIFF_RATIO = 0.003;
const VIEWPORT = { width: 1280, height: 720 };

const scenarios = [
  {
    name: 'default-small-graph.png',
    fixture: 'small-graph.json',
  },
  {
    name: 'selected-node.png',
    fixture: 'small-graph.json',
    action: async (page) => {
      await clickNode(page, 'app/main.py');
    },
  },
  {
    name: 'search-active.png',
    fixture: 'small-graph.json',
    action: async (page) => {
      await page.getByTestId('btn-search').click();
      await page.getByTestId('search-input').fill('util');
    },
  },
  {
    name: 'collapsed-left-rail.png',
    fixture: 'small-graph.json',
    action: async (page) => {
      await page.getByTestId('btn-waste').click();
    },
  },
  {
    name: 'cycles-panel-open.png',
    fixture: 'cycles-graph.json',
    action: async (page) => {
      await page.getByTestId('btn-cycles').click();
    },
  },
];

async function isServerUp() {
  try {
    const response = await fetch(VISUALIZER_URL, { method: 'GET' });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForServer(timeoutMs = 120_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await isServerUp()) return;
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${VISUALIZER_URL}`);
}

function startServer() {
  const python = process.platform === 'win32'
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python');
  const child = spawn(python, ['analyzer.py', '.', '--serve', '--port', '8766'], {
    cwd: ROOT,
    stdio: 'inherit',
  });
  return child;
}

async function loadFixture(page, fixtureName) {
  const fixture = JSON.parse(await readFile(path.join(FIXTURES_DIR, fixtureName), 'utf8'));
  await page.goto(VISUALIZER_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.loadGraph === 'function' && !!window.__orbitsDebug);
  await page.evaluate((graph) => {
    window.loadGraph(graph);
  }, fixture);
  await page.waitForFunction((count) => {
    const debug = window.__orbitsDebug;
    return debug?.graphLoaded && debug.graphNodeCount === count && debug.visibleNodeCount > 0;
  }, fixture.nodes.length, { timeout: 45_000 });
}

async function getNodeCanvasPoint(page, nodeId) {
  const point = await page.evaluate((id) => window.__orbitsDebug.getNodeCanvasPoint(id), nodeId);
  if (!point) throw new Error(`Could not locate node ${nodeId}`);
  return point;
}

async function clickNode(page, nodeId) {
  const point = await getNodeCanvasPoint(page, nodeId);
  await page.getByTestId('graph-canvas').click({ position: point });
}

async function settle(page, ms = 700) {
  await page.waitForTimeout(ms);
}

async function capture(page) {
  return page.locator('#app').screenshot({
    animations: 'disabled',
    caret: 'hide',
  });
}

async function assertSnapshot(name, actualPng) {
  await mkdir(SNAPSHOT_DIR, { recursive: true });
  const baselinePath = path.join(SNAPSHOT_DIR, name);

  if (UPDATE) {
    await writeFile(baselinePath, actualPng);
    return;
  }

  const expectedPng = PNG.sync.read(await readFile(baselinePath));
  const actual = PNG.sync.read(actualPng);
  if (expectedPng.width !== actual.width || expectedPng.height !== actual.height) {
    throw new Error(`Visual baseline size mismatch for ${name}`);
  }

  const diff = new PNG({ width: expectedPng.width, height: expectedPng.height });
  const diffPixels = pixelmatch(expectedPng.data, actual.data, diff.data, expectedPng.width, expectedPng.height, {
    threshold: 0.1,
  });
  const diffRatio = diffPixels / (expectedPng.width * expectedPng.height);
  if (diffRatio > MAX_DIFF_RATIO) {
    await mkdir(DIFF_DIR, { recursive: true });
    const diffPath = path.join(DIFF_DIR, name.replace(/\.png$/i, '.diff.png'));
    await writeFile(diffPath, PNG.sync.write(diff));
    throw new Error(
      `Visual regression for ${name}: ${(diffRatio * 100).toFixed(2)}% exceeds ${(MAX_DIFF_RATIO * 100).toFixed(2)}%. Diff: ${path.relative(ROOT, diffPath)}`,
    );
  }
}

async function run() {
  let serverProcess = null;
  if (!(await isServerUp())) {
    serverProcess = startServer();
    await waitForServer();
  }

  const browser = await chromium.launch({
    headless: true,
    channel: process.platform === 'win32' ? 'msedge' : undefined,
  });
  try {
    const context = await browser.newContext({ viewport: VIEWPORT });
    const page = await context.newPage();

    for (const scenario of scenarios) {
      await loadFixture(page, scenario.fixture);
      if (scenario.action) {
        await scenario.action(page);
      }
      await settle(page);
      const png = await capture(page);
      await assertSnapshot(scenario.name, png);
      console.log(`ok  ${scenario.name}`);
    }

    await context.close();
  } finally {
    await browser.close();
    if (serverProcess) {
      serverProcess.kill();
      await delay(250);
    }
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
