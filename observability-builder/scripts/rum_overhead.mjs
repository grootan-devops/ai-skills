#!/usr/bin/env node
// Browser RUM overhead test (references/overhead-and-security.md, "Browser RUM").
//
// Loads the same page with RUM on and off, under a CPU slowdown, and compares FCP, LCP, total
// blocking time, the slowest interaction and console errors against the budget. With --on-dist and
// --off-dist it also compares the gzip size of the initial JavaScript (scripts and modulepreloads
// in index.html) and of the lazily loaded chunks.
//
//   node rum_overhead.mjs --on http://localhost:8080/ --off http://localhost:8081/ \
//     [--click '#place'] [--wait-for '#total'] [--runs 8] [--cpu 4] \
//     [--on-dist dist --off-dist dist-norum] [--json out.json]
//
// Playwright comes from the project (npm i -D playwright, then npx playwright install chromium).
// Exit 0 within budget, 1 over budget, 2 on a usage or run error.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const BUDGET = {
  initial_js_kb: 50, // gzip KB added to the initial JavaScript
  lazy_js_kb: 60, // gzip KB of chunks loaded after start-up
  paint_pct: 5, // FCP and LCP: +5% or +50 ms, whichever is larger
  paint_ms: 50,
  tbt_ms: 50, // total blocking time added during load and the scripted interaction
  inp_ms: 16, // slowest interaction (event timing) added
  console_errors: 0,
};

function args(argv) {
  const out = { runs: 8, cpu: 4 };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    if (key === 'help') out.help = true;
    else out[key] = argv[++i];
  }
  out.runs = Number(out.runs);
  out.cpu = Number(out.cpu);
  return out;
}

// Files of one build; a nested build (a sub-directory with its own index.html) is another app.
function walk(dir, top = true) {
  if (!top && readdirSync(dir).includes('index.html')) return [];
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path, false) : [path];
  });
}

function bundle(dist) {
  const html = readFileSync(join(dist, 'index.html'), 'utf8');
  const initial = new Set([...html.matchAll(/(?:src|href)="\/?([^"]+\.m?js)"/g)].map((m) => m[1].replace(/^.*?(assets\/)/, '$1')));
  let init = 0;
  let lazy = 0;
  for (const file of walk(dist).filter((f) => /\.m?js$/.test(f))) {
    const kb = gzipSync(readFileSync(file)).length / 1024;
    const rel = relative(dist, file);
    if ([...initial].some((i) => rel.endsWith(i))) init += kb;
    else lazy += kb;
  }
  return { initial_js_kb: Math.round(init * 10) / 10, lazy_js_kb: Math.round(lazy * 10) / 10 };
}

async function once(chromium, url, opt) {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: opt.cpu });
    let errors = 0;
    page.on('console', (m) => { if (m.type() === 'error') errors++; });
    await page.addInitScript(() => {
      const m = (window.__rumOverhead = { tbt: 0, lcp: 0, inp: 0 });
      new PerformanceObserver((l) => l.getEntries().forEach((e) => (m.tbt += Math.max(0, e.duration - 50)))).observe({ type: 'longtask', buffered: true });
      new PerformanceObserver((l) => l.getEntries().forEach((e) => (m.lcp = e.startTime))).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver((l) => l.getEntries().forEach((e) => (m.inp = Math.max(m.inp, e.duration)))).observe({ type: 'event', durationThreshold: 16, buffered: true });
    });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(2000);
    if (opt.click) {
      await page.click(opt.click);
      if (opt.waitFor) await page.waitForSelector(opt.waitFor);
      await page.waitForTimeout(500);
    }
    const m = await page.evaluate(() => ({
      ...window.__rumOverhead,
      fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0,
    }));
    return { ...m, errors };
  } finally {
    await browser.close();
  }
}

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];

async function main() {
  const opt = args(process.argv.slice(2));
  if (opt.help || !opt.on || !opt.off) {
    console.log(readFileSync(new URL(import.meta.url), 'utf8').split('\n').slice(1, 14).join('\n').replace(/^\/\/ ?/gm, ''));
    return opt.help ? 0 : 2;
  }
  let chromium;
  try {
    ({ chromium } = createRequire(join(process.cwd(), 'package.json'))('playwright'));
  } catch {
    console.error('playwright not found in this project: npm i -D playwright && npx playwright install chromium');
    return 2;
  }
  const result = { budget: BUDGET, runs: opt.runs, cpu_slowdown: opt.cpu };
  for (const [name, url] of [['off', opt.off], ['on', opt.on]]) {
    const runs = [];
    for (let i = 0; i < opt.runs; i++) runs.push(await once(chromium, url, opt));
    result[name] = Object.fromEntries(['fcp', 'lcp', 'tbt', 'inp', 'errors'].map((k) => [k, Math.round(median(runs.map((r) => r[k])) * 10) / 10]));
  }
  const d = (k) => Math.round((result.on[k] - result.off[k]) * 10) / 10;
  const paintLimit = (k) => Math.max((result.off[k] * BUDGET.paint_pct) / 100, BUDGET.paint_ms);
  const checks = {
    fcp: d('fcp') <= paintLimit('fcp'),
    lcp: d('lcp') <= paintLimit('lcp'),
    tbt: d('tbt') <= BUDGET.tbt_ms,
    inp: d('inp') <= BUDGET.inp_ms,
    console_errors: d('errors') <= BUDGET.console_errors,
  };
  if (opt.onDist && opt.offDist) {
    const on = bundle(opt.onDist);
    const off = bundle(opt.offDist);
    result.bundle = { on, off };
    checks.initial_js = on.initial_js_kb - off.initial_js_kb <= BUDGET.initial_js_kb;
    checks.lazy_js = on.lazy_js_kb - off.lazy_js_kb <= BUDGET.lazy_js_kb;
  }
  result.delta = Object.fromEntries(['fcp', 'lcp', 'tbt', 'inp', 'errors'].map((k) => [k, d(k)]));
  result.checks = checks;
  result.pass = Object.values(checks).every(Boolean);
  const text = JSON.stringify(result, null, 2);
  console.log(text);
  if (opt.json) (await import('node:fs')).writeFileSync(opt.json, text + '\n');
  return result.pass ? 0 : 1;
}

main().then((code) => process.exit(code), (err) => { console.error(err?.message ?? err); process.exit(2); });
