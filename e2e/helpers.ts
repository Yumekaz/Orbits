import { expect, Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

export type DebugSnapshot = {
  graphLoaded: boolean;
  graphNodeCount: number;
  graphEdgeCount: number;
  graphDynamicEdgeCount: number;
  selectedNodeId: string | null;
  visibleNodeCount: number;
  visibleEdgeCount: number;
  visibleDynamicEdgeCount: number;
  edgeMode: string;
  menuStates: {
    langOpen: boolean;
    filterOpen: boolean;
    layoutOpen: boolean;
    searchOpen: boolean;
  };
  zoom: { x: number; y: number; k: number };
  shellState: {
    leftRailCollapsed: boolean;
    inspectorCollapsed: boolean;
  };
  performanceState: {
    perfMode: string;
    motionEnabled: boolean;
    layoutMode: string;
    showFullGraph: boolean;
  };
};

function fixturePath(name: string): string {
  return path.join(process.cwd(), 'e2e', 'fixtures', name);
}

export async function gotoVisualizer(page: Page): Promise<void> {
  await page.goto('/visualizer.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof (window as any).loadGraph === 'function' && !!(window as any).__orbitsDebug);
}

export async function getDebug(page: Page): Promise<DebugSnapshot> {
  return page.evaluate(() => ({
    graphLoaded: (window as any).__orbitsDebug.graphLoaded,
    graphNodeCount: (window as any).__orbitsDebug.graphNodeCount,
    graphEdgeCount: (window as any).__orbitsDebug.graphEdgeCount,
    graphDynamicEdgeCount: (window as any).__orbitsDebug.graphDynamicEdgeCount,
    selectedNodeId: (window as any).__orbitsDebug.selectedNodeId,
    visibleNodeCount: (window as any).__orbitsDebug.visibleNodeCount,
    visibleEdgeCount: (window as any).__orbitsDebug.visibleEdgeCount,
    visibleDynamicEdgeCount: (window as any).__orbitsDebug.visibleDynamicEdgeCount,
    edgeMode: (window as any).__orbitsDebug.edgeMode,
    menuStates: (window as any).__orbitsDebug.menuStates,
    zoom: (window as any).__orbitsDebug.zoom,
    shellState: (window as any).__orbitsDebug.shellState,
    performanceState: (window as any).__orbitsDebug.performanceState,
  }));
}

export async function waitForGraphLoaded(page: Page, expectedNodes?: number): Promise<DebugSnapshot> {
  await page.waitForFunction((count) => {
    const debug = (window as any).__orbitsDebug;
    if (!debug?.graphLoaded) return false;
    return typeof count === 'number' ? debug.graphNodeCount === count : true;
  }, expectedNodes, { timeout: 45_000 });
  await expect.poll(async () => (await getDebug(page)).visibleNodeCount, { timeout: 15_000 }).toBeGreaterThan(0);
  return getDebug(page);
}

export async function loadFixture<T = any>(page: Page, fixtureName: string): Promise<T> {
  const fixture = JSON.parse(await readFile(fixturePath(fixtureName), 'utf8')) as T & { nodes?: unknown[] };
  await gotoVisualizer(page);
  await page.evaluate((graph) => {
    (window as any).loadGraph(graph);
  }, fixture);
  await waitForGraphLoaded(page, fixture.nodes?.length);
  return fixture;
}

export async function sampleCanvasPixels(page: Page): Promise<number> {
  return page.getByTestId('graph-canvas').evaluate((canvas) => {
    const el = canvas as HTMLCanvasElement;
    const ctx = el.getContext('2d');
    if (!ctx || !el.width || !el.height) return 0;
    const data = ctx.getImageData(0, 0, el.width, el.height).data;
    const stride = Math.max(4, Math.floor((el.width * el.height) / 4000)) * 4;
    let lit = 0;
    for (let i = 3; i < data.length; i += stride) {
      if (data[i] > 0) lit += 1;
    }
    return lit;
  });
}

export async function getNodeCanvasPoint(page: Page, nodeId: string): Promise<{ x: number; y: number }> {
  const point = await page.evaluate((id) => (window as any).__orbitsDebug.getNodeCanvasPoint(id), nodeId);
  expect(point).not.toBeNull();
  return point as { x: number; y: number };
}

async function canvasViewportPoint(page: Page, point: { x: number; y: number }): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId('graph-canvas').boundingBox();
  if (!box) throw new Error('Graph canvas is not visible');
  return { x: box.x + point.x, y: box.y + point.y };
}

export async function clickNode(page: Page, nodeId: string): Promise<void> {
  const point = await getNodeCanvasPoint(page, nodeId);
  await page.getByTestId('graph-canvas').click({ position: point });
}

export async function dragNode(page: Page, nodeId: string, dx: number, dy: number): Promise<void> {
  const point = await getNodeCanvasPoint(page, nodeId);
  const start = await canvasViewportPoint(page, point);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + dx, start.y + dy, { steps: 10 });
  await page.mouse.up();
}

export async function panCanvas(page: Page, start: { x: number; y: number }, end: { x: number; y: number }): Promise<void> {
  const startPoint = await canvasViewportPoint(page, start);
  const endPoint = await canvasViewportPoint(page, end);
  await page.mouse.move(startPoint.x, startPoint.y);
  await page.mouse.down();
  await page.mouse.move(endPoint.x, endPoint.y, { steps: 10 });
  await page.mouse.up();
}

export async function wheelCanvas(page: Page, point: { x: number; y: number }, deltaY: number): Promise<void> {
  const target = await canvasViewportPoint(page, point);
  await page.mouse.move(target.x, target.y);
  await page.mouse.wheel(0, deltaY);
}

export async function openMenu(page: Page, testId: string): Promise<void> {
  await page.getByTestId(testId).click();
}

