// Requires running Compose, system Google Chrome, and disposable local schedule data.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome',
  headless: true,
  args: ['--no-sandbox'],
});
const page = await browser.newPage();
page.on('dialog', dialog => dialog.accept());
page.on('pageerror', error => console.error('Browser error:', error));
const vehicle = `E${randomUUID().slice(0, 10)}`;
let serviceId;
let oldDuration;

async function addPath(nodes) {
  for (const node of nodes) {
    await page.locator('select[name="next"]').selectOption(node);
    await page.getByRole('button', { name: 'Add to path' }).click();
  }
}
async function noError() {
  assert.equal(await page.locator('[role="alert"]').count(), 0,
    (await page.locator('[role="alert"]').allTextContents()).join('; '));
}

try {
  await page.goto(`${baseURL}/editor`);
  await page.getByRole('heading', { name: 'Schedule Editor' }).waitFor();
  assert.equal(await page.locator('input[name="passenger"]').count(), 0, 'no repositioning toggle');
  await page.locator('input[name="newId"]').fill(vehicle);
  await page.locator('input[name="newName"]').fill('Smoke vehicle');
  await page.getByRole('button', { name: 'Add vehicle' }).click();
  await page.locator('li').filter({ hasText: `${vehicle} — Smoke vehicle` }).first().waitFor();
  await page.locator('select[name="vehicle"]').selectOption(vehicle);
  for (const node of ['Y', 'B1']) {
    await page.locator('app-track-map').locator(`[aria-label="Add ${node} to path"]`).click();
  }
  await page.getByRole('alert').getByText('Path ends at a block', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Create', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Preview change' }).click();
  await page.getByRole('alert').getByText('Path ends at a block', { exact: false }).waitFor();
  await page.locator('app-track-map').locator('[aria-label="Add P1A to path"]').click();
  const yardDwell = page.locator('ol li').first().locator('input[type="number"]');
  await yardDwell.fill('24');
  await page.getByRole('button', { name: 'Preview change' }).click();
  await page.getByText(/Y: arrival .*battery 82/).waitFor();
  await yardDwell.fill('0');
  await page.locator('app-track-map .node.active').nth(2).waitFor();
  assert.equal(await page.locator('app-track-map .node.active').count(), 3);
  assert.equal(await page.locator('app-track-map .edge.active').count() >= 2, true);
  // Directed connectivity: Y cannot be appended directly after P1A.
  await page.locator('app-track-map .node').filter({ hasText: /^Y$/ }).click();
  assert.equal(await page.locator('ol li').count(), 3);
  const countBeforePreview = (await (await page.request.get(`${baseURL}/api/schedule`)).json()).timelines.length;
  await page.getByRole('button', { name: 'Preview change' }).click();
  await page.getByRole('heading', { name: /Candidate preview/ }).waitFor();
  assert.equal((await (await page.request.get(`${baseURL}/api/schedule`)).json()).timelines.length,
    countBeforePreview, 'service preview must not write');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.getByText('Y → B1 → P1A').first().waitFor();
  await noError();
  const response = await (await page.request.get(`${baseURL}/api/schedule`)).json();
  serviceId = response.timelines.find(t => t.service.vehicle_id === vehicle)?.service.id;
  assert.ok(serviceId, 'service persisted');
  await page.reload();
  await page.getByText(`#${serviceId} · ${vehicle}`).waitFor();
  await page.goto(`${baseURL}/viewer`);
  await page.getByRole('heading', { name: 'Schedule Viewer (read-only)' }).waitFor();
  await page.getByRole('heading', { name: `#${serviceId} · ${vehicle}` }).waitFor();
  assert.ok(await page.getByText('P1A').count());
  await page.getByRole('button', { name: 'Auto (alternative)' }).click();
  await page.getByText('No scheduled services.').waitFor();
  await page.getByRole('button', { name: 'Manual' }).click();
  await page.getByRole('heading', { name: `#${serviceId} · ${vehicle}` }).waitFor();
  await page.locator('app-playback').getByText(`${vehicle} · B1 · Service #${serviceId}`, { exact: false }).waitFor();
  const seek = page.locator('app-playback input[type=range]');
  await seek.evaluate(el => { el.value = '60'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.locator('app-playback').getByText(`${vehicle} · P1A · Idle`, { exact: false }).waitFor();
  await noError();

  await page.goto(`${baseURL}/editor`);
  await page.getByText(`#${serviceId} · ${vehicle}`).waitFor();
  const entry = page.locator('article').filter({ hasText: `#${serviceId} · ${vehicle}` });
  await entry.getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('button', { name: 'Clear' }).click();
  await addPath(['P1A', 'B3', 'B5', 'P2A']);
  await page.getByRole('button', { name: 'Update' }).click();
  await page.getByText('LOCATION_DISCONTINUITY').first().waitFor();
  assert.match(await page.locator('main').innerText(), /draft/);
  await page.goto(`${baseURL}/viewer`);
  await page.locator('app-playback').getByText(`Position/battery unknown for: ${vehicle}`, { exact: false }).waitFor();
  await page.locator('app-playback').getByText('LOCATION_DISCONTINUITY', { exact: false }).waitFor();
  assert.equal(await page.locator('app-playback .vehicle').count(), 0);
  await page.goto(`${baseURL}/editor`);
  const repairEntry = page.locator('article').filter({ hasText: `#${serviceId} · ${vehicle}` });
  await repairEntry.getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('button', { name: 'Clear' }).click();
  await addPath(['Y', 'B1', 'P1A']);
  await page.getByRole('button', { name: 'Update' }).click();
  await noError();
  await page.getByText('validated').first().waitFor();
  // Playback shows overlapping block occupancy at t=0 and clears it at the
  // half-open boundary t=60. Keep this temporary conflict out of later tests.
  const otherVehicle = `E${randomUUID().slice(0, 10)}`;
  const scenarioStart = (await (await page.request.get(`${baseURL}/api/schedule`)).json()).scenario_start_at;
  assert.equal((await page.request.post(`${baseURL}/api/vehicles`, {
    data: { id: otherVehicle, name: 'Playback conflict' },
  })).status(), 201);
  const overlapping = await page.request.post(`${baseURL}/api/services`, { data: {
    vehicle_id: otherVehicle, start_at: scenarioStart,
    steps: [{ node: 'Y' }, { node: 'B1' }, { node: 'P1A' }],
  } });
  assert.equal(overlapping.status(), 201);
  await page.goto(`${baseURL}/viewer`);
  await page.locator('app-playback').getByText('BLOCK_OVERLAP', { exact: false }).waitFor();
  assert.equal(await page.locator('app-playback .vehicle').count(), 2);
  await page.locator('app-playback input[type=range]').evaluate(el => {
    el.value = '60'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.locator('app-playback').getByText('Conflicts at this instant').waitFor({ state: 'hidden' });
  await page.request.delete(`${baseURL}/api/services/${(await overlapping.json()).id}`);
  await page.request.delete(`${baseURL}/api/vehicles/${otherVehicle}`);

  await page.goto(`${baseURL}/editor`);
  await page.locator('article').filter({ hasText: `#${serviceId} · ${vehicle}` })
    .getByRole('button', { name: 'Edit' }).click();
  await page.locator('ol li').first().locator('input[type="number"]').fill('24');
  await page.getByRole('button', { name: 'Update' }).click();
  await page.goto(`${baseURL}/viewer`);
  await page.locator('app-playback input[type=range]').evaluate(el => {
    el.value = '12'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.locator('app-playback').getByText(`${vehicle} · Y · Service #${serviceId} · Battery 81.00`).waitFor();
  await page.goto(`${baseURL}/editor`);
  await page.locator('article').filter({ hasText: `#${serviceId} · ${vehicle}` })
    .getByRole('button', { name: 'Edit' }).click();
  await page.locator('ol li').first().locator('input[type="number"]').fill('0');
  await page.getByRole('button', { name: 'Update' }).click();

  await page.goto(`${baseURL}/configuration`);
  await page.getByRole('heading', { name: 'Block Configuration' }).waitFor();
  const row = page.locator('.block-card').filter({ hasText: /^B14/ });
  oldDuration = Number(await row.locator('input').inputValue());
  const autoBeforeConfig = (await (await page.request.get(`${baseURL}/api/schedule?scenario=auto`)).json()).revision;
  await row.locator('input').fill(String(oldDuration + 1));
  await row.getByRole('button', { name: 'Preview' }).click();
  await page.getByRole('heading', { name: /Preview B14/ }).waitFor();
  assert.equal((await page.request.get(`${baseURL}/api/blocks`)).ok(), true);
  assert.equal((await (await page.request.get(`${baseURL}/api/blocks`)).json()).B14, oldDuration,
    'preview must not write');
  await page.getByRole('button', { name: 'Confirm and save' }).click();
  await page.getByRole('heading', { name: /Preview B14/ }).waitFor({ state: 'hidden' });
  assert.equal((await (await page.request.get(`${baseURL}/api/blocks`)).json()).B14, oldDuration + 1);
  assert.equal((await (await page.request.get(`${baseURL}/api/schedule?scenario=auto`)).json()).revision,
    autoBeforeConfig + 1, 'shared configuration revalidates auto');
  await noError();
  await page.goto(`${baseURL}/editor`);
  await page.getByText(`#${serviceId} · ${vehicle}`).waitFor();
  await page.locator('article').filter({ hasText: `#${serviceId} · ${vehicle}` })
    .getByRole('button', { name: 'Delete' }).click();
  await page.getByText(`#${serviceId} · ${vehicle}`).waitFor({ state: 'hidden' });
  assert.equal((await (await page.request.get(`${baseURL}/api/schedule`)).json())
    .timelines.some(t => t.service.id === serviceId), false);
  await page.locator('li').filter({ hasText: `${vehicle} — Smoke vehicle` })
    .getByRole('button', { name: 'Delete' }).click();
  await page.locator('li').filter({ hasText: `${vehicle} — Smoke vehicle` }).waitFor({ state: 'hidden' });
  await noError();
  console.log('PASS: UI vehicle/service CRUD, draft repair, reload, viewer, service/block preview/commit');
} finally {
  // Best-effort cleanup, retaining error visibility if cleanup fails.
  if (oldDuration !== undefined) {
    const s = await (await page.request.get(`${baseURL}/api/schedule`)).json();
    const a = await (await page.request.get(`${baseURL}/api/schedule?scenario=auto`)).json();
    await page.request.put(`${baseURL}/api/blocks/B14`, {
      data: { traversal_seconds: oldDuration, expected_revision: s.revision,
              expected_auto_revision: a.revision },
    });
  }
  if (serviceId) await page.request.delete(`${baseURL}/api/services/${serviceId}`);
  await page.request.delete(`${baseURL}/api/vehicles/${vehicle}`);
  await browser.close();
}
