import test from 'node:test';
import assert from 'node:assert/strict';
import { sessionSummary } from '../presentation.mjs';

test('initial state never reports an unobserved scene as clear', () => {
  assert.equal(sessionSummary().heading, 'Waiting for detection results');
  assert.equal(sessionSummary().assistance, '');
});
test('hazard count uses only visible blocked/partially blocked exits', () => {
  const summary = sessionSummary({ video_time_s: 1, exits: [
    { visible: true, status: 'obstructed' }, { visible: true, status: 'partially_obstructed' },
    { visible: false, status: 'obstructed' }, { visible: true, status: 'clear' },
  ] });
  assert.equal(summary.heading, '2 exit hazards detected');
});
test('pending exits show checking instead of a confirmed clear result', () => {
  assert.equal(sessionSummary({ video_time_s: 1, exits: [{ visible: true, status: 'unknown' }] }).heading,
    'Checking exits • no confirmed hazards yet');
});
test('Qwen unavailable overrides contradictory dates and confirmations', () => {
  const summary = sessionSummary({ video_time_s: 1, qwen_active: false, date_reading_enabled: true,
    extinguishers: [{ id: 3, status: 'valid', status_text: 'VALID', due_text: 'June 2027', confirmed: true }] });
  assert.equal(summary.qwen, false);
  assert.match(summary.assistance, /date verification is disabled/);
  assert.equal(summary.counts, '1 extinguishers visible • Dates not verified');
  assert.deepEqual(summary.lines, ['Extinguisher #3 · DATE NOT VERIFIED · No date read · unconfirmed']);
});
test('date summary matches Android, including confirmed and provisional results', () => {
  const summary = sessionSummary({ video_time_s: 2, qwen_active: true, date_reading_enabled: true,
    extinguishers: [
      { id: 1, status: 'expired', status_text: 'EXPIRED', due_text: 'June 2025', confirmed: true },
      { id: 2, status: 'expiring_soon', status_text: 'DUE SOON', due_text: 'June 2026', confirmed: false },
    ] });
  assert.equal(summary.heading, 'No exit hazards detected');
  assert.equal(summary.assistance, 'Qwen assistance active');
  assert.equal(summary.counts, '2 extinguishers visible • 1 expired / overdue • 1 due soon');
  assert.match(summary.lines[0], /June 2025 · confirmed$/);
  assert.match(summary.lines[1], /June 2026 · unconfirmed$/);
});
test('unread dates keep the Android no-date fallback', () => {
  const summary = sessionSummary({ qwen_active: true, date_reading_enabled: true,
    extinguishers: [{ id: 5, status: 'reading', status_text: 'READING', confirmed: false }] });
  assert.equal(summary.lines[0], 'Extinguisher #5 · READING · No date read · unconfirmed');
});
