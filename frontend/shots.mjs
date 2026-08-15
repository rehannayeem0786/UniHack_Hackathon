/**
 * Capture dashboard screenshots for the submission deck.
 *
 * Drives the real UI in the installed browser and writes PNGs to docs/shots/.
 * Screenshots in a deck should be of the running product, not mockups, so this
 * is scripted and repeatable rather than hand-cropped.
 *
 * Usage:  node shots.mjs      (with the service running on :8000)
 */
import { existsSync, mkdirSync } from "node:fs";
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8000";
const OUT = "../docs/shots";

const CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
];
const executablePath = CANDIDATES.find((p) => existsSync(p));
if (!executablePath) {
  console.error("No Edge or Chrome found.");
  process.exit(2);
}

mkdirSync(OUT, { recursive: true });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function tab(page, label) {
  const [t] = await page.$$(
    `xpath///button[@role="tab"][contains(., ${JSON.stringify(label)})]`,
  );
  if (!t) throw new Error(`tab not found: ${label}`);
  await t.click();
  await wait(900);
}

/** Screenshot a single element, with a little breathing room around it. */
async function shotOf(page, selector, name, { index = 0, pad = 12 } = {}) {
  const handles = await page.$$(selector);
  const el = handles[index];
  if (!el) {
    console.warn(`  ! skipped ${name} (no match for ${selector})`);
    return;
  }
  await el.scrollIntoView();
  await wait(450);
  const box = await el.boundingBox();
  if (!box) {
    console.warn(`  ! skipped ${name} (no box)`);
    return;
  }
  const vp = page.viewport();
  await page.screenshot({
    path: `${OUT}/${name}.png`,
    clip: {
      x: Math.max(0, box.x - pad),
      y: Math.max(0, box.y - pad),
      width: Math.min(vp.width - Math.max(0, box.x - pad), box.width + pad * 2),
      height: Math.min(vp.height - Math.max(0, box.y - pad), box.height + pad * 2),
    },
  });
  console.log(`  -> ${name}.png`);
}

const browser = await puppeteer.launch({
  executablePath,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=2"],
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 2 });
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForFunction(
    () => (document.querySelector("#root")?.childElementCount ?? 0) > 0,
    { timeout: 30000 },
  );
  await wait(1200);

  console.log("Enrich tab");
  await page.screenshot({ path: `${OUT}/01-hero.png` });
  console.log("  -> 01-hero.png");

  // Run one row so the record view is populated.
  const [run] = await page.$$('xpath///button[contains(., "Run enrichment pipeline")]');
  await run.click();
  await page.waitForFunction(
    () => /Five description surfaces/.test(document.body.textContent ?? ""),
    { timeout: 180000 },
  );
  await wait(1200);

  await shotOf(page, '[role="tabpanel"][data-state="active"] .grid > div', "02-metrics", {
    index: 0,
  });

  // Raw input vs resolved identity — the most persuasive frame.
  const cards = await page.$$('[role="tabpanel"][data-state="active"] .grid');
  for (const [i, card] of cards.entries()) {
    const text = await card.evaluate((el) => el.textContent ?? "");
    if (text.includes("Raw input") && text.includes("Resolved identity")) {
      await card.scrollIntoView();
      await wait(400);
      const box = await card.boundingBox();
      if (box) {
        await page.screenshot({
          path: `${OUT}/03-input-vs-output.png`,
          clip: { x: box.x - 10, y: box.y - 10, width: box.width + 20, height: Math.min(box.height + 20, 1080) },
        });
        console.log(`  -> 03-input-vs-output.png (card ${i})`);
      }
      break;
    }
  }

  // Five surfaces.
  const sections = await page.$$('[role="tabpanel"][data-state="active"] section');
  for (const section of sections) {
    const text = await section.evaluate((el) => el.textContent ?? "");
    if (text.startsWith("Five description surfaces")) {
      await section.scrollIntoView();
      await wait(400);
      const box = await section.boundingBox();
      if (box) {
        await page.screenshot({
          path: `${OUT}/04-surfaces.png`,
          clip: { x: box.x - 10, y: box.y - 10, width: box.width + 20, height: Math.min(box.height + 20, 1060) },
        });
        console.log("  -> 04-surfaces.png");
      }
      break;
    }
    if (text.startsWith("Attributes")) {
      await section.scrollIntoView();
      await wait(400);
      const box = await section.boundingBox();
      if (box) {
        await page.screenshot({
          path: `${OUT}/05-attributes.png`,
          clip: { x: box.x - 10, y: box.y - 10, width: box.width + 20, height: Math.min(box.height + 20, 900) },
        });
        console.log("  -> 05-attributes.png");
      }
    }
  }

  // Provenance panel.
  const provCards = await page.$$('[role="tabpanel"][data-state="active"] .grid > div');
  for (const card of provCards) {
    const text = await card.evaluate((el) => el.textContent ?? "");
    if (text.includes("Provenance")) {
      await card.scrollIntoView();
      await wait(400);
      const box = await card.boundingBox();
      if (box) {
        await page.screenshot({
          path: `${OUT}/06-provenance.png`,
          clip: { x: box.x - 10, y: box.y - 10, width: box.width + 20, height: Math.min(box.height + 20, 900) },
        });
        console.log("  -> 06-provenance.png");
      }
      break;
    }
  }

  console.log("Evaluation tab");
  await tab(page, "Evaluation");
  const [runEval] = await page.$$('xpath///button[contains(., "Run evaluation")]');
  if (runEval) {
    await runEval.click();
    await page.waitForSelector(".recharts-bar-rectangle", { timeout: 240000 });
    await wait(1500);
    await page.evaluate(() => window.scrollTo(0, 0));
    await wait(400);
    await page.screenshot({ path: `${OUT}/07-evaluation.png` });
    console.log("  -> 07-evaluation.png");
    await shotOf(page, ".recharts-wrapper", "08-chart");
  }

  console.log("Learned rules tab");
  await tab(page, "Learned rules");
  await page.waitForFunction(
    () => /Description grammar by category/.test(document.body.textContent ?? ""),
    { timeout: 60000 },
  );
  await wait(1500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await wait(400);
  await page.screenshot({ path: `${OUT}/09-learned-rules.png` });
  console.log("  -> 09-learned-rules.png");

  console.log("Batch tab");
  await tab(page, "Batch");
  await page.screenshot({ path: `${OUT}/10-batch.png` });
  console.log("  -> 10-batch.png");

  console.log("\nDone. Files in docs/shots/");
} finally {
  await browser.close();
}
