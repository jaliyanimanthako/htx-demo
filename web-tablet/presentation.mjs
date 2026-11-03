// Mirrors Android's session summary. Boxes/labels arrive already drawn by the backend.
export function sessionSummary(state = {}) {
  const dates = state.date_reading_enabled === true && state.qwen_active !== false;
  const qwen = typeof state.qwen_active === 'boolean' ? state.qwen_active
    : typeof state.date_reading_enabled === 'boolean' ? dates : null;
  const exits = (state.exits || []).filter(item => item.visible);
  const hazards = exits.filter(item => ['obstructed', 'partially_obstructed'].includes(item.status)).length;
  const known = state.video_time_s != null;
  const heading = !known ? 'Waiting for detection results' : hazards > 0
    ? `${hazards} exit hazards detected` : exits.some(item => item.status === 'unknown')
      ? 'Checking exits • no confirmed hazards yet' : 'No exit hazards detected';
  const extinguishers = (state.extinguishers || []).map(item => dates ? item : {
    ...item, status: 'unverified', status_text: 'DATE NOT VERIFIED', due_text: null, confirmed: false,
  });
  return {
    heading, qwen,
    assistance: qwen === false ? 'Qwen assistance unavailable — date verification is disabled. Detection continues normally.'
      : qwen === true ? 'Qwen assistance active' : '',
    counts: `${extinguishers.length} extinguishers visible • ` + (!dates ? 'Dates not verified'
      : `${extinguishers.filter(item => item.status === 'expired').length} expired / overdue • ${extinguishers.filter(item => item.status === 'expiring_soon').length} due soon`),
    lines: extinguishers.map(item => `Extinguisher #${item.id} · ${item.status_text} · ${item.due_text || 'No date read'} · ${item.confirmed ? 'confirmed' : 'unconfirmed'}`),
  };
}
