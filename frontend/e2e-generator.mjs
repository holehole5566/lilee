// Destructive auto replacement: run ONLY against a disposable Compose database.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const base = process.env.E2E_BASE_URL ?? 'http://localhost:8080';
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome',
  headless: true, args: ['--no-sandbox'],
});
const page = await browser.newPage();
page.on('dialog', dialog => dialog.accept());
const vehicle = `E${randomUUID().slice(0, 10)}`;
try {
  const before = await (await page.request.get(`${base}/api/schedule?scenario=auto`)).json();
  assert.equal(before.timelines.length, 0, 'only run on an empty disposable auto scenario');
  const manual = await (await page.request.get(`${base}/api/schedule`)).json();
  assert.equal((await page.request.post(`${base}/api/vehicles`, {
    data: { id: vehicle, name: 'Generator smoke vehicle' },
  })).status(), 201);
  // Creating a shared vehicle advances both revisions, but not manual services.
  const manualAfterVehicle = await (await page.request.get(`${base}/api/schedule`)).json();
  await page.goto(`${base}/generator`);
  await page.getByRole('heading', { name: 'Auto Schedule Generator' }).waitFor();
  await page.getByLabel(`${vehicle} — Generator smoke vehicle`).check();
  const origin = before.scenario_start_at.slice(0, 10);
  // The test DB's scenario start must be midnight UTC; use a one-minute offset.
  assert.match(before.scenario_start_at, /T00:00:00/);
  await page.locator('[name=rangeStart]').fill(`${origin}T00:01`);
  await page.locator('[name=rangeEnd]').fill(`${origin}T00:01`);
  await page.locator('[name=interval]').fill('600');
  await page.getByRole('button', { name: 'Preview auto candidate' }).click();
  await page.getByRole('heading', { name: /Auto candidate/ }).waitFor();
  assert.match(await page.locator('main').innerText(), /Complete \(not saved\)/);
  assert.equal((await (await page.request.get(`${base}/api/schedule?scenario=auto`)).json()).timelines.length, 0);
  await page.getByRole('button', { name: 'Replace auto schedule' }).click();
  await page.getByText(/Saved \d+ auto services at revision/).waitFor();
  const saved = await (await page.request.get(`${base}/api/schedule?scenario=auto`)).json();
  assert.ok(saved.timelines.length);
  assert.ok(saved.timelines.every(t => t.service.steps.every(step =>
    step.dwell_seconds === (step.node.startsWith('P') ? 60 : 0))));
  assert.equal(saved.status, 'validated');
  assert.deepEqual(await (await page.request.get(`${base}/api/schedule`)).json(), manualAfterVehicle);
  assert.deepEqual(manual.timelines, manualAfterVehicle.timelines);
  // Incomplete coverage remains a preview, never an actionable commit.
  await page.locator('[name=interval]').fill('1');
  await page.getByRole('button', { name: 'Preview auto candidate' }).click();
  await page.getByText('Incomplete — cannot commit').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Replace auto schedule' }).isDisabled(), true);
  assert.deepEqual(await (await page.request.get(`${base}/api/schedule?scenario=auto`)).json(), saved);
  // A shared fleet change advances auto revision after a successful preview.
  await page.locator('[name=interval]').fill('600');
  await page.getByRole('button', { name: 'Preview auto candidate' }).click();
  await page.getByText('Complete (not saved)').waitFor();
  assert.equal((await page.request.post(`${base}/api/vehicles`, {
    data: { id: `${vehicle}X`, name: 'Revision invalidator' },
  })).status(), 201);
  await page.getByRole('button', { name: 'Replace auto schedule' }).click();
  await page.getByRole('alert').getByText(/STALE_REVISION/).waitFor();
  assert.equal(await page.getByRole('heading', { name: /Auto candidate/ }).count(), 0);
  const afterStale = await (await page.request.get(`${base}/api/schedule?scenario=auto`)).json();
  assert.equal(afterStale.revision, saved.revision + 1);
  assert.deepEqual(afterStale.timelines, saved.timelines);
  await page.getByRole('link', { name: 'Schedule Viewer', exact: true }).click();
  await page.getByRole('button', { name: 'Auto (alternative)' }).click();
  await page.getByRole('heading', { name: new RegExp(vehicle) }).first().waitFor();
  assert.equal(await page.locator('[role=alert]').count(), 0);
  console.log('PASS: generator preview/no-write, atomic auto save, viewer, manual unchanged');
} finally {
  await browser.close();
}
