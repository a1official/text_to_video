import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..", "..");
const runtimeRoot = path.join(repoRoot, "runtime", "playwright-captures", "traveyclub");
const rawRoot = path.join(runtimeRoot, "raw");

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function newestFile(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files = await Promise.all(
    entries
      .filter((entry) => entry.isFile())
      .map(async (entry) => {
        const fullPath = path.join(dir, entry.name);
        const stat = await fs.stat(fullPath);
        return { fullPath, mtimeMs: stat.mtimeMs };
      }),
  );
  files.sort((a, b) => b.mtimeMs - a.mtimeMs);
  if (!files.length) {
    throw new Error(`No files found in ${dir}`);
  }
  return files[0].fullPath;
}

async function renameNewestVideo(tempDir, finalName) {
  const sourcePath = await newestFile(tempDir);
  const targetPath = path.join(rawRoot, finalName);
  await fs.copyFile(sourcePath, targetPath);
  return targetPath;
}

async function recordSegment(name, perform) {
  const browser = await chromium.launch({ headless: true });
  const videoDir = path.join(rawRoot, `${name}-tmp`);
  await ensureDir(videoDir);
  const context = await browser.newContext({
    viewport: { width: 1600, height: 900 },
    recordVideo: {
      dir: videoDir,
      size: { width: 1600, height: 900 },
    },
  });

  const page = await context.newPage();
  await page.goto("https://traveyclub.com/", { waitUntil: "networkidle", timeout: 60000 });
  await page.mouse.move(700, 420);
  await page.waitForTimeout(1000);
  await perform(page);
  await page.waitForTimeout(1200);
  await context.close();
  await browser.close();

  return renameNewestVideo(videoDir, `${name}.webm`);
}

async function main() {
  await ensureDir(rawRoot);

  const heroPath = await recordSegment("hero", async (page) => {
    await page.mouse.move(810, 330, { steps: 20 });
    await page.waitForTimeout(900);
    await page.mouse.move(1030, 330, { steps: 16 });
    await page.waitForTimeout(900);
    await page.mouse.wheel(0, 420);
    await page.waitForTimeout(1400);
  });

  const itineraryPath = await recordSegment("itinerary-grid", async (page) => {
    await page.mouse.wheel(0, 740);
    await page.waitForTimeout(1600);
    await page.mouse.move(290, 820, { steps: 18 });
    await page.waitForTimeout(700);
    await page.mouse.move(870, 820, { steps: 18 });
    await page.waitForTimeout(700);
    await page.mouse.wheel(0, 260);
    await page.waitForTimeout(1200);
  });

  console.log(JSON.stringify({ heroPath, itineraryPath }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
