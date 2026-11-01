export async function fetchBundle() {
  const r = await fetch('/api/bundle');
  if (!r.ok) throw new Error('bundle missing — run `python bear_reversal/run_daily.py`');
  return r.json();
}

/** Candles since 2014 for any NSE stock; tf 'D' (daily) or 'W' (weekly bars). */
export async function fetchStock(symbol, asOf = null, tf = 'D') {
  const q = new URLSearchParams();
  if (asOf) q.set('asof', asOf);
  if (tf === 'W') q.set('tf', 'W');
  const r = await fetch(`/api/stock/${encodeURIComponent(symbol)}${q.toString() ? `?${q}` : ''}`);
  if (!r.ok) return null;
  return r.json();
}

export async function fetchReplayIndex() {
  const r = await fetch('/api/replay');
  if (!r.ok) return null;
  return r.json();
}

export async function fetchReplayWeek(date) {
  const r = await fetch(`/api/replay/${date}`);
  if (!r.ok) return null;
  return r.json();
}

export async function fetchReplayMatrix() {
  const r = await fetch('/api/replay/matrix');
  if (!r.ok) return null;
  return r.json();
}

export async function fetchSector(name) {
  const r = await fetch(`/api/sector/${encodeURIComponent(name)}`);
  if (!r.ok) return null;
  return r.json();
}

export async function fetchNextBullWatch() {
  const r = await fetch('/api/next-bull-watch');
  if (!r.ok) return null;
  return r.json();
}

/** Spends LLM tokens (one model call) unless a cached note exists — call only on a click. */
export async function requestAiNote(symbol, { refresh = false, deep = false } = {}) {
  const q = new URLSearchParams();
  if (refresh) q.set('refresh', '1');
  if (deep) q.set('deep', '1');
  const r = await fetch(`/api/ai-note/${encodeURIComponent(symbol)}${q.toString() ? `?${q}` : ''}`,
    { method: 'POST' });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return r.ok ? j : { error: j.error || `HTTP ${r.status}` };
}

export async function requestAiBrief(refresh = false) {
  const r = await fetch(`/api/ai-brief${refresh ? '?refresh=1' : ''}`, { method: 'POST' });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return r.ok ? j : { error: j.error || `HTTP ${r.status}` };
}

/** Research dossier (signal facts + checklist + markdown). No LLM, no cost. */
export async function fetchDossier(symbol) {
  const r = await fetch(`/api/dossier/${encodeURIComponent(symbol)}`);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return r.ok ? j : { error: j.error || `HTTP ${r.status}` };
}

/** Signal ledger: one row per signal episode + your decisions (signal_ledger.py). */
export async function fetchLedger() {
  const r = await fetch('/api/ledger');
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return r.ok ? j : { error: j.error || `HTTP ${r.status}` };
}

async function postJson(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return r.ok ? j : { error: j.error || `HTTP ${r.status}` };
}

export const saveLedgerEntry = (id, patch) => postJson(`/api/ledger/${encodeURIComponent(id)}`, patch);
export const saveLedgerSettings = (patch) => postJson('/api/ledger-settings', patch);

export async function fetchMoverStructure() {
  const r = await fetch('/api/mover-structure');
  if (!r.ok) return null;
  return r.json();
}

export async function fetchMoversStudy(lane = 'A') {
  const r = await fetch(`/api/movers-study?lane=${lane}`);
  if (!r.ok) return null;
  return r.json();
}

export const fmtPct = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '–' : `${(v * 100).toFixed(digits)}%`;
export const fmtPctRaw = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '–' : `${v.toFixed(digits)}%`;
export const fmtNum = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '–' : Number(v).toFixed(digits);
