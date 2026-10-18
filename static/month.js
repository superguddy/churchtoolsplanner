'use strict';

// Monatsuebersicht: alle Events eines Monats fuer eine Gruppe planen und gemeinsam speichern.
// Wie in der Planung: Antworten werden nummeriert (seq) und veraltete verworfen; Aenderungen sind
// nur vorgemerkt (pending), bis "Speichern" sie mit Konfliktpruefung nach ChurchTools schreibt.

const $ = (id) => document.getElementById(id);

const state = {
  groups: [],
  groupId: null,
  month: null,          // "JJJJ-MM"
  view: null,
  seq: 0,
  pending: new Map(),   // "eventId:requestId" -> {event_id, request_id, person_id, expected}
  suggestions: new Map(), // "eventId:requestId" -> {person_id, name, reason}
  suggestionsStale: false,
  saving: false,
};

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.method && opts.method !== 'GET') {
    opts.headers['X-Planner'] = '1';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, opts);
  let body = null;
  try { body = await resp.json(); } catch (_) { /* leer */ }
  if (resp.status === 401) { window.location.href = '/'; throw new Error('Nicht angemeldet.'); }
  if (!resp.ok) throw new Error((body && body.detail) || `Fehler ${resp.status}`);
  return body;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'selected' || k === 'disabled') node[k] = v;
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const key = (eventId, requestId) => `${eventId}:${requestId}`;
const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];

function fmtDate(iso) {
  if (!iso) return '–';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function shiftMonth(month, delta) {
  const [y, m] = month.split('-').map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function setStatus(text) { $('saveStatus').textContent = text || ''; }

function confirmDiscard() {
  return state.pending.size === 0 || window.confirm('Es gibt nicht gespeicherte Änderungen. Verwerfen?');
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.groupId) params.set('group', state.groupId);
  params.set('month', state.month);
  history.replaceState(null, '', `/monat?${params}`);
  try { localStorage.setItem('planner_month_group', String(state.groupId || '')); } catch (_) { /* egal */ }
}

// ------------------------------------------------------------------ Laden
async function load() {
  const seq = ++state.seq;
  const [y, m] = state.month.split('-').map(Number);
  $('monthLabel').textContent = `${MONTHS[m - 1]} ${y}`;
  updateUrl();
  state.view = null;
  state.suggestions = new Map();
  render();
  if (!state.groupId) return;
  $('monthBody').replaceChildren(el('tr', {}, el('td', { class: 'loading' }, 'Lade Monat …')));
  try {
    const view = await api(`/api/month?group_id=${state.groupId}&month=${state.month}`);
    if (seq !== state.seq) return; // veraltete Antwort
    applyView(view);
  } catch (err) {
    if (seq === state.seq) setStatus(`Fehler beim Laden: ${err.message}`);
  }
}

function applyView(view) {
  if (view.group_id !== state.groupId || view.month !== state.month) return;
  state.view = view;
  state.suggestions = new Map(view.suggestions.map((s) => [key(s.event_id, s.request_id), s]));
  state.suggestionsStale = false;
  render();
}

async function recalcSuggestions() {
  const seq = ++state.seq;
  $('recalcBtn').disabled = true;
  try {
    const res = await api('/api/month/suggest', {
      method: 'POST',
      body: { group_id: state.groupId, month: state.month, changes: [...state.pending.values()] },
    });
    if (seq !== state.seq) return;
    state.suggestions = new Map(res.suggestions.map((s) => [key(s.event_id, s.request_id), s]));
    state.suggestionsStale = false;
    render();
  } catch (err) {
    setStatus(`Fehler: ${err.message}`);
  } finally {
    $('recalcBtn').disabled = false;
  }
}

// ------------------------------------------------------------------ Zustand je Zelle
function currentPersonId(ev, row) {
  const p = state.pending.get(key(ev.id, row.request_id));
  if (p) return p.person_id;
  return row.person ? row.person.id : null;
}

function removeOnce(arr, item) {
  const i = arr.indexOf(item);
  if (i >= 0) arr.splice(i, 1);
}

// Personen, die in diesem Event (inkl. Vormerkungen) schon einen anderen Dienst haben
function othersInEvent(ev, row) {
  const out = new Map(Object.entries(ev.in_event).map(([pid, labels]) => [Number(pid), [...labels]]));
  for (const r of ev.rows) {
    const orig = r.person && r.person.id;
    const now = currentPersonId(ev, r);
    if (orig === now) continue;
    if (orig && out.has(orig)) removeOnce(out.get(orig), r.label);
    if (now) out.set(now, [...(out.get(now) || []), r.label]);
  }
  const mine = currentPersonId(ev, row);
  if (mine && out.has(mine)) removeOnce(out.get(mine), row.label);
  for (const [pid, labels] of out) if (!labels.length) out.delete(pid);
  return out;
}

function monthCounts() {
  const counts = new Map();
  for (const ev of (state.view && state.view.events) || []) {
    for (const row of ev.rows) {
      const pid = currentPersonId(ev, row);
      if (pid) counts.set(pid, (counts.get(pid) || 0) + 1);
    }
  }
  return counts;
}

function activeSuggestion(ev, row) {
  if (state.suggestionsStale || currentPersonId(ev, row)) return null;
  const sug = state.suggestions.get(key(ev.id, row.request_id));
  if (!sug) return null;
  // Person inzwischen in diesem Event anderweitig vorgemerkt? Dann nicht mehr anbieten.
  if (ev.rows.some((r) => currentPersonId(ev, r) === sug.person_id)) return null;
  return sug;
}

function setPerson(ev, row, personId, { ask = true } = {}) {
  const k = key(ev.id, row.request_id);
  const orig = row.person ? row.person.id : null;
  if (personId && ask) {
    const person = state.view.persons.find((p) => p.id === personId);
    const isInternService = (state.view.intern_services || []).includes(row.label);
    if (person.rule.intern && !isInternService) {
      alert(`${person.name} ist als Praktikant/in markiert und kann nur in Praktikanten-Dienste eingeteilt werden.`);
      return false;
    }
    if (!person.rule.intern && isInternService
        && !confirm(`${person.name} ist nicht als Praktikant/in markiert. Trotzdem in „${row.label}“ einteilen?`)) return false;
    const others = othersInEvent(ev, row).get(personId);
    if (others && !confirm(`${person.name} ist in diesem Event schon eingeteilt (${others.join(', ')}). Trotzdem?`)) return false;
    const max = person.rule.max_per_month;
    const count = monthCounts().get(personId) || 0;
    if (max && count >= max && person.rule.limit_hard) {
      alert(`${person.name}: verbindliches Monatslimit erreicht (${count}/${max}).`);
      return false;
    }
    if (max && count >= max && !confirm(`${person.name} hat das Monatslimit erreicht (${count}/${max}). Trotzdem?`)) return false;
  }
  if (personId === orig) state.pending.delete(k);
  else state.pending.set(k, { event_id: ev.id, request_id: row.request_id, person_id: personId, expected_person_id: orig });
  state.suggestionsStale = state.suggestionsStale || ask; // manuelle Aenderung -> Vorschlaege neu berechnen
  return true;
}

// ------------------------------------------------------------------ Rendern
function render() {
  renderTable();
  renderSummary();
  const dirty = state.pending.size > 0;
  $('saveBtn').disabled = !dirty || state.saving;
  $('discardBtn').disabled = !dirty || state.saving;
  if (dirty && !state.saving) setStatus(`${state.pending.size} Änderung(en) nicht gespeichert.`);
  const open = openSuggestions();
  $('suggestAllBtn').hidden = !open.length;
  $('suggestAllBtn').textContent = open.length === 1 ? 'Vorschlag übernehmen' : `Alle ${open.length} Vorschläge übernehmen`;
  $('recalcBtn').hidden = !state.view || !state.suggestionsStale;
}

function openSuggestions() {
  const out = [];
  for (const ev of (state.view && state.view.events) || []) {
    for (const row of ev.rows) {
      const sug = activeSuggestion(ev, row);
      if (sug) out.push([ev, row, sug]);
    }
  }
  return out;
}

function renderTable() {
  const view = state.view;
  if (!state.groupId) {
    $('monthHead').replaceChildren();
    $('monthBody').replaceChildren(el('tr', {}, el('td', { class: 'empty' }, 'Gruppe wählen.')));
    return;
  }
  if (!view) return;
  $('monthHead').replaceChildren(el('tr', {}, el('th', {}, 'Event'), ...view.labels.map((l) => el('th', {}, l))));
  if (!view.events.length) {
    $('monthBody').replaceChildren(el('tr', {}, el('td', { class: 'empty', colspan: String(view.labels.length + 1) }, 'Keine Events in diesem Monat.')));
    return;
  }
  const counts = monthCounts();
  $('monthBody').replaceChildren(...view.events.map((ev) => el('tr', { class: ev.past ? 'past' : '' },
    el('td', { class: 'evname' }, ev.display, ev.past ? el('div', { class: 'muted small' }, 'vergangen')
      : el('div', {}, el('button', {
        class: 'linklike small', title: 'Dienste zum Event hinzufügen oder entfernen (wie in ChurchTools)',
        onclick: () => editServices(ev),
      }, 'Dienste bearbeiten …'))),
    ...view.labels.map((label) => {
      const rows = ev.rows.filter((r) => r.label === label);
      if (!rows.length) return el('td', { class: 'muted small' }, 'kein Dienst');
      return el('td', {}, ...rows.map((row, i) => cell(ev, row, rows.length > 1 ? i + 1 : null, counts)));
    }))));
}

function cell(ev, row, slot, counts) {
  const k = key(ev.id, row.request_id);
  const pid = currentPersonId(ev, row);
  const others = othersInEvent(ev, row);
  const absent = new Set(ev.absent);
  const options = [el('option', { value: '' }, '—')];
  const persons = [...state.view.persons];
  // Externe/fremde Person, die aktuell eingetragen ist, trotzdem anzeigen
  if (row.person && !persons.some((p) => p.id === row.person.id)) {
    persons.push({ id: row.person.id, name: row.person.name, rule: {}, foreign: true });
  }
  const isInternService = (state.view.intern_services || []).includes(row.label);
  for (const p of persons) {
    const marks = [];
    let disabled = false;
    if (p.rule.intern && !isInternService) { marks.push('nur Praktikanten-Dienste'); disabled = true; }
    if (p.rule.intern && isInternService) marks.push('Praktikant');
    if (absent.has(p.id)) { marks.push('abwesend'); disabled = true; }
    if (ev.blocked[String(p.id)]) { marks.push(`Regel: ${ev.blocked[String(p.id)]}`); disabled = true; }
    if (others.has(p.id)) marks.push(`schon: ${others.get(p.id).join(', ')}`);
    const c = counts.get(p.id) || 0;
    const isSel = p.id === pid;
    if (p.rule.max_per_month && c >= p.rule.max_per_month) {
      marks.push(`Limit ${c}/${p.rule.max_per_month}${p.rule.limit_hard ? ' verbindlich' : ''}`);
      if (p.rule.limit_hard && !isSel) disabled = true;
    } else if (c) marks.push(`${c}×`);
    options.push(el('option', { value: String(p.id), selected: isSel, disabled: disabled && !isSel },
      p.name + (marks.length ? ` (${marks.join(', ')})` : '')));
  }
  const external = row.person && row.person.external && !state.pending.has(k);
  const select = el('select', {
    'aria-label': `${ev.display} – ${row.label}${slot ? ` ${slot}` : ''}`,
    disabled: state.saving || external || ev.past,
    onchange: (e) => {
      const value = e.target.value ? Number(e.target.value) : null;
      if (!setPerson(ev, row, value)) e.target.value = pid == null ? '' : String(pid);
      render();
    },
  }, ...options);
  const blockedNow = pid && (absent.has(pid) || ev.blocked[String(pid)]);
  const sug = activeSuggestion(ev, row);
  return el('div', { class: `mcell ${state.pending.has(k) ? 'changed' : ''} ${blockedNow ? 'conflict' : ''}` },
    slot ? el('span', { class: 'muted small' }, `${slot}. `) : null,
    external ? el('span', { class: 'extern' }, `${row.person.name} (extern)`) : select,
    row.person && row.person.agreed === false && !state.pending.has(k) ? el('span', { class: 'badge pending' }, 'angefragt') : null,
    sug ? el('button', {
      class: 'suggestion small linklike', title: sug.reason,
      onclick: () => { setPerson(ev, row, sug.person_id, { ask: false }); render(); },
    }, `Vorschlag: ${sug.name} (${sug.reason})`) : null);
}

function renderSummary() {
  const view = state.view;
  if (!view) { $('summaryBody').replaceChildren(); return; }
  const counts = monthCounts();
  $('summaryBody').replaceChildren(...view.persons.map((p) => {
    const c = counts.get(p.id) || 0;
    const over = p.rule.max_per_month && c > p.rule.max_per_month;
    return el('tr', { class: c === 0 ? 'zero' : '' },
      el('td', { title: p.rule.summary ? `Regel: ${p.rule.summary}` : '' }, p.name, p.rule.summary ? ` (${p.rule.summary})` : ''),
      el('td', { class: over ? 'error' : '', title: p.rule.limit_hard ? 'verbindliches Limit' : '' },
        p.rule.max_per_month ? `${c}/${p.rule.max_per_month}${p.rule.limit_hard ? ' !' : ''}` : c),
      el('td', {}, fmtDate(p.last_before)));
  }));
}

function editServices(ev) {
  if (state.pending.size && !confirm('Es gibt nicht gespeicherte Einteilungen. Diese gehen dabei verloren. Weiter?')) return;
  window.openServiceSetup({
    api, eventId: ev.id, groupId: state.groupId, title: ev.display,
    onSaved: () => { state.pending.clear(); setStatus('Dienste in ChurchTools geändert.'); load(); },
  });
}

// ------------------------------------------------------------------ Aktionen
$('groupSelect').addEventListener('change', (e) => {
  if (!confirmDiscard()) { e.target.value = String(state.groupId || ''); return; }
  state.groupId = Number(e.target.value) || null;
  state.pending.clear();
  load();
});

for (const [id, delta] of [['prevMonth', -1], ['nextMonth', 1]]) {
  $(id).addEventListener('click', () => {
    if (!confirmDiscard()) return;
    state.month = shiftMonth(state.month, delta);
    state.pending.clear();
    load();
  });
}

$('suggestAllBtn').addEventListener('click', () => {
  for (const [ev, row, sug] of openSuggestions()) {
    if (activeSuggestion(ev, row)) setPerson(ev, row, sug.person_id, { ask: false });
  }
  render();
});

$('recalcBtn').addEventListener('click', recalcSuggestions);

$('discardBtn').addEventListener('click', () => {
  state.pending.clear();
  setStatus('');
  recalcSuggestions();
  render();
});

$('saveBtn').addEventListener('click', async () => {
  if (!state.pending.size) return;
  state.saving = true;
  setStatus('Speichern …');
  render();
  const groupId = state.groupId;
  const month = state.month;
  try {
    const res = await api('/api/month/save', { method: 'POST', body: { group_id: groupId, month, changes: [...state.pending.values()] } });
    state.pending.clear();
    state.seq++;
    applyView(res.view);
    setStatus(res.errors.length ? `Teilweise gespeichert (${res.saved}).` : `Gespeichert (${res.saved}).`);
    if (res.errors.length) alert(res.errors.join('\n'));
  } catch (err) {
    setStatus('Fehler beim Speichern.');
    alert(`Speichern fehlgeschlagen: ${err.message}`);
  } finally {
    state.saving = false;
    render();
  }
});

window.addEventListener('beforeunload', (e) => {
  if (state.pending.size) { e.preventDefault(); e.returnValue = ''; }
});

// ------------------------------------------------------------------ Start
async function init() {
  const s = await api('/api/session');
  if (!s.logged_in) { window.location.href = '/'; return; }
  $('userInfo').textContent = `${s.username} · ${s.base_url}`;
  state.groups = await api('/api/groups');
  const params = new URLSearchParams(window.location.search);
  let stored = null;
  try { stored = localStorage.getItem('planner_month_group'); } catch (_) { /* egal */ }
  const wanted = Number(params.get('group') || stored) || null;
  state.groupId = state.groups.some((g) => g.id === wanted) ? wanted : null;
  const now = new Date();
  state.month = /^\d{4}-\d{2}$/.test(params.get('month') || '') ? params.get('month')
    : `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  $('groupSelect').replaceChildren(el('option', { value: '' }, 'Gruppe wählen …'),
    ...state.groups.map((g) => el('option', { value: String(g.id), selected: g.id === state.groupId }, g.name)));
  await load();
}

init().catch((err) => setStatus(`Fehler: ${err.message}`));
