/**
 * Headless render check.
 *
 * A build that compiles is not a build that runs: an empty vendor chunk once let
 * the bundle build cleanly and then fail to mount, which no type check or Rollup
 * warning catches. This loads the real page in the installed browser, fails on
 * any console error or unhandled rejection, and asserts that each tab actually
 * renders content.
 */
import { existsSync } from "node:fs";
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8000";

const CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];

const executablePath = CANDIDATES.find((p) => existsSync(p));
if (!executablePath) {
  console.error("No Edge or Chrome binary found; skipping render check.");
  process.exit(2);
}

/** Activate a tab and wait for its lazy panel to render. Returns its length. */
async function clickTab(page, label) {
  const [tab] = await page.$$(
    `xpath///button[@role="tab"][contains(., ${JSON.stringify(label)})]`,
  );
  if (!tab) return null;
  await tab.click();
  await page.waitForFunction(
    () => {
      const panel = document.querySelector('[role="tabpanel"][data-state="active"]');
      return (panel?.textContent?.trim().length ?? 0) > 40;
    },
    { timeout: 30000 },
  );
  return page.$eval(
    '[role="tabpanel"][data-state="active"]',
    (el) => el.textContent?.trim().length ?? 0,
  );
}

const problems = [];
const browser = await puppeteer.launch({
  executablePath,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu"],
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => {
    const url = request.url();
    // Ignore the remote webfont: it is a nicety and may be blocked offline.
    if (!url.includes("rsms.me")) {
      problems.push(`requestfailed: ${url} ${request.failure()?.errorText ?? ""}`);
    }
  });

  const response = await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  console.log(`GET ${BASE} -> ${response?.status()}`);

  // The app has mounted only when React has put something inside #root.
  await page.waitForFunction(
    () => (document.querySelector("#root")?.childElementCount ?? 0) > 0,
    { timeout: 30000 },
  );

  const heading = await page.$eval("main h2", (el) => el.textContent?.trim() ?? "");
  console.log(`mounted, heading: "${heading}"`);

  // Proves the browser can actually reach the API, not just that React mounted.
  try {
    await page.waitForFunction(
      () => /Live/.test(document.querySelector("header")?.textContent ?? ""),
      { timeout: 60000 },
    );
    const status = await page.$eval("header", (el) =>
      (el.textContent ?? "").replace(/\s+/g, " ").trim(),
    );
    console.log(`header status: ${status}`);
  } catch {
    problems.push("health status never reached 'Live' — is the API reachable?");
  }

  // Each tab is a separate lazy chunk; visiting them proves the chunks resolve.
  for (const label of ["Batch", "Evaluation", "Learned rules"]) {
    const chars = await clickTab(page, label);
    if (chars === null) problems.push(`tab not found: ${label}`);
    else console.log(`tab "${label}" rendered (${chars} chars)`);
  }

  // Exercise the batch flow twice. Running it a second time is the point: a
  // stale poll from the first job once made the second job fetch its results
  // while it was still queued, surfacing a 409 that only interaction reveals.
  await clickTab(page, "Batch");
  for (const attempt of [1, 2]) {
    await page.$eval('input[type="number"]', (el) => {
      el.value = "";
    });
    await page.type('input[type="number"]', "3");

    const [start] = await page.$$('xpath///button[contains(., "Start run")]');
    if (!start) {
      problems.push("Start run button not found");
      break;
    }
    await start.click();

    await page.waitForFunction(
      () => {
        const panel = document.querySelector('[role="tabpanel"][data-state="active"]');
        return /Scored against ground truth/.test(panel?.textContent ?? "");
      },
      { timeout: 120000 },
    );

    const alerts = await page.$$eval('[role="alert"]', (nodes) =>
      nodes.map((n) => (n.textContent ?? "").replace(/\s+/g, " ").trim()),
    );
    if (alerts.length) {
      problems.push(`run ${attempt} surfaced an error alert: ${alerts.join(" | ")}`);
    }

    const scores = await page.$eval('[role="tabpanel"][data-state="active"]', (el) => {
      const text = (el.textContent ?? "").replace(/\s+/g, " ");
      const match = text.match(/Mean exact match\s*([\d.]+%)\s*Mean fuzzy match\s*([\d.]+%)/);
      return match ? `exact ${match[1]}, fuzzy ${match[2]}` : "(scores not parsed)";
    });
    console.log(`batch run ${attempt}: complete, no error alert, ${scores}`);
  }

  // Run the evaluation and check the chart tooltip is actually legible. Recharts
  // colours its default tooltip text from each bar's fill, which silently
  // produced dark-on-dark text; a contrast assertion catches that class of bug.
  await clickTab(page, "Evaluation");
  const [runEval] = await page.$$('xpath///button[contains(., "Run evaluation")]');
  if (!runEval) {
    problems.push("Run evaluation button not found");
  } else {
    await runEval.click();
    await page.waitForSelector(".recharts-bar-rectangle", { timeout: 180000 });

    // Hover by coordinate with intermediate steps: an SVG bar has no clickable
    // box for hover(), and Recharts tracks mousemove on the chart surface, so a
    // single jumped position does not register.
    const surface = await page.$(".recharts-surface");
    // Mouse coordinates are viewport-relative, so a chart below the fold must be
    // scrolled into view or the pointer lands nowhere.
    await surface.scrollIntoView();
    await new Promise((resolve) => setTimeout(resolve, 400));
    const box = await surface.boundingBox();
    if (!box) throw new Error("chart surface has no bounding box");
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.move(box.x + box.width * 0.08, box.y + box.height * 0.6, {
      steps: 12,
    });
    await page.waitForFunction(
      () => (document.querySelector(".recharts-tooltip-wrapper")?.textContent ?? "").length > 8,
      { timeout: 15000 },
    );

    const report = await page.evaluate(() => {
      // Relative luminance and contrast ratio, per WCAG 2.1.
      const channel = (v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      const parse = (value) => {
        const m = value.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const [r, g, b, a = "1"] = m[1].split(",").map((p) => parseFloat(p));
        return { r, g, b, a: Number(a) };
      };
      const luminance = (c) =>
        0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
      const contrast = (a, b) => {
        const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
      };
      // Walk up for the nearest opaque background.
      const backgroundOf = (el) => {
        let node = el;
        while (node) {
          const bg = parse(getComputedStyle(node).backgroundColor);
          if (bg && bg.a > 0.5) return bg;
          node = node.parentElement;
        }
        return { r: 10, g: 15, b: 25, a: 1 };
      };

      const root = document.querySelector(".recharts-tooltip-wrapper");
      const out = [];
      for (const el of root.querySelectorAll("*")) {
        // An element's *own* text, ignoring text owned by descendants. This
        // catches a label that sits next to a coloured swatch span, which a
        // leaf-only walk would skip entirely.
        const text = Array.from(el.childNodes)
          .filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => n.textContent ?? "")
          .join("")
          .trim();
        if (!text) continue;
        const fg = parse(getComputedStyle(el).color);
        if (!fg) continue;
        out.push({ text, ratio: Number(contrast(fg, backgroundOf(el)).toFixed(2)) });
      }
      return out;
    });

    const unreadable = report.filter((item) => item.ratio < 4.5);
    for (const item of report) {
      console.log(`tooltip "${item.text}" contrast ${item.ratio}:1`);
    }
    if (unreadable.length) {
      problems.push(
        "tooltip text below 4.5:1 contrast: " +
          unreadable.map((i) => `"${i.text}" (${i.ratio}:1)`).join(", "),
      );
    }
  }

  await clickTab(page, "Enrich");
  await page.screenshot({ path: "smoke.png", fullPage: false });
  console.log("screenshot -> frontend/smoke.png");
} catch (error) {
  problems.push(`fatal: ${error.message}`);
} finally {
  await browser.close();
}

if (problems.length) {
  console.error("\nFAILED");
  for (const problem of problems) console.error("  - " + problem);
  process.exit(1);
}
console.log("\nPASS - the dashboard mounts and every tab renders.");
