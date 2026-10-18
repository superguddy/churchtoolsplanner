'use strict';

// ChurchTools Planer – Oberflaeche.
// Wichtig gegen das alte WPF-Problem (Einteilungen eines anderen Events blieben rechts stehen):
// Jede Ansicht wird mit einer laufenden Nummer angefordert. Antworten, die nicht mehr zur
// aktuellen Auswahl gehoeren, werden verworfen, und die rechte Spalte wird beim Wechsel sofort geleert.

const $ = (id) => document.getElementById(id);

const state = {
  groups: [],
  events: [],
  groupId: null,
  eventId: null,
  view: null,          // letzte gueltige /api/view-Antwort
  viewSeq: 0,
  statsSeq: 0,
  pending: new Map(),  // request_id -> {person_id (null = austragen), name, expected (Person beim Laden)}
  selectedPersonId: null,
  saving: false,
};

// ------------------------------------------------------------------ API
async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.method && opts.method !== 'GET') {
    opts.headers['X-Planner'] = '1';
    if (opts.body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
  }
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* leer */ }
  if (resp.status === 401 && path !== '/api/login') {
    showLogin();
    throw new Error('Nicht angemeldet.');
  }
  if (!resp.ok) throw new Error((data && data.detail) || `Fehler ${resp.status}`);
  return data;
}

// ------------------------------------------------------------------ Hilfen
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function fmtDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function badge(text, cls = '') { return el('span', { class: `badge ${cls}` }, text); }

function setStatus(text) { $('saveStatus').textContent = text || ''; }

function personsById() {
  const map = new Map();
  const persons = state.view && state.view.group_id === state.groupId ? state.view.persons : [];
  for (const p of persons || []) map.set(p.id, p);
  return map;
}

// ------------------------------------------------------------------ Login
async function init() {
  const s = await api('/api/session');
  if (s.logged_in) return showMain(s);
  showLogin(s);
}

function showLogin(s) {
  $('mainView').hidden = true;
  $('loginView').hidden = false;
  $('logoutBtn').hidden = true;
  $('reloadBtn').hidden = true;
  $('userInfo').textContent = '';
  if (s) {
    $('loginTarget').textContent = s.base_url;
    $('loginUser').value = s.saved_username || '';
    $('loginRemember').checked = !!s.saved_username;
    $('savedPwHint').hidden = !s.has_saved_password;
    $('loginRemember').parentElement.lastChild.textContent = s.can_remember_password
      ? ' Anmeldedaten merken' : ' Benutzername merken';
    (s.saved_username ? $('loginPass') : $('loginUser')).focus();
  }
}

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('loginError').textContent = '';
  $('loginBtn').disabled = true;
  try {
    const s = await api('/api/login', {
      method: 'POST',
      body: { username: $('loginUser').value, password: $('loginPass').value, remember: $('loginRemember').checked },
    });
    $('loginPass').value = '';
    await showMain(s);
  } catch (err) {
    $('loginError').textContent = err.message;
  } finally {
    $('loginBtn').disabled = false;
  }
});

$('logoutBtn').addEventListener('click', async () => {
  if (!confirmDiscard()) return;
  state.pending.clear();
  const s = await api('/api/logout', { method: 'POST' });
  showLogin(s);
});

$('reloadBtn').addEventListener('click', async () => {
  if (!confirmDiscard()) return;
  state.pending.clear();
  await api('/api/reload', { method: 'POST' });
  await loadLists();
  refreshView();
  refreshStats();
});

async function showMain(s) {
  $('loginView').hidden = true;
  $('mainView').hidden = false;
  $('logoutBtn').hidden = false;
  $('reloadBtn').hidden = false;
  $('userInfo').textContent = `${s.username} · ${s.base_url}`;
  await loadLists();
  renderAll();
}

async function loadLists() {
  const [groups, events] = await Promise.all([api('/api/groups'), api('/api/events')]);
  state.groups = groups;
  state.events = events;
  if (!groups.some((g) => g.id === state.groupId)) state.groupId = null;
  if (!events.some((e) => e.id === state.eventId)) state.eventId = null;
  renderGroups();
  renderEvents(true);
}

// ------------------------------------------------------------------ Auswahl
function confirmDiscard() {
  if (state.pending.size === 0) return true;
  return window.confirm('Es gibt nicht gespeicherte Änderungen. Verwerfen?');
}

function selectGroup(id) {
  if (state.groupId === id) return;
  state.groupId = id;
  $('monthLink').href = `/monat?group=${id}`;
  state.selectedPersonId = null;
  renderGroups();
  refreshView();
  refreshStats();
}

function selectEvent(id) {
  if (state.eventId === id || state.saving) return;
  if (!confirmDiscard()) return;
  state.eventId = id;
  state.pending.clear();
  renderEvents(false);
  refreshView();
}

async function refreshView() {
  const seq = ++state.viewSeq;
  const groupId = state.groupId;
  const eventId = state.eventId;
  // Sofort leeren: nie Daten des vorherigen Events/der vorherigen Gruppe stehen lassen.
  if (!state.view || state.view.event_id !== eventId) renderServices(eventId ? 'Lade Dienste …' : null);
  if (!state.view || state.view.group_id !== groupId) renderPersons(groupId ? 'Lade Personen …' : null);
  if (!groupId && !eventId) { state.view = null; renderAll(); return; }

  const params = new URLSearchParams();
  if (groupId) params.set('group_id', groupId);
  if (eventId) params.set('event_id', eventId);
  try {
    const view = await api(`/api/view?${params}`);
    if (seq !== state.viewSeq) return; // veraltete Antwort
    applyView(view);
  } catch (err) {
    if (seq !== state.viewSeq) return;
    setStatus(`Fehler beim Laden: ${err.message}`);
    renderServices(eventId ? 'Fehler beim Laden.' : null);
  }
}

function applyView(view) {
  // Zweite Sicherung: nur uebernehmen, wenn die Antwort zur aktuellen Auswahl passt.
  if (view.event_id !== state.eventId || view.group_id !== state.groupId) return false;
  state.view = view;
  renderPersons();
  renderServices();
  return true;
}

async function refreshStats() {
  const seq = ++state.statsSeq;
  const groupId = state.groupId;
  const tbody = $('statsTable').querySelector('tbody');
  tbody.replaceChildren();
  if (!groupId) { $('statsStatus').textContent = ''; return; }
  $('statsStatus').textContent = 'Berechnung läuft …';
  try {
    const stats = await api(`/api/groups/${groupId}/stats`);
    if (seq !== state.statsSeq || stats.group_id !== state.groupId) return;
    tbody.replaceChildren(...stats.rows.map((r) => el('tr', {},
      el('td', {}, r.name), el('td', {}, r.last_month), el('td', {}, r.current_month),
      el('td', {}, r.next_month), el('td', {}, r.year_to_date), el('td', {}, r.year_full))));
    $('statsStatus').textContent = `Stand: ${stats.as_of}`;
  } catch (err) {
    if (seq === state.statsSeq) $('statsStatus').textContent = `Fehler: ${err.message}`;
  }
}

// ------------------------------------------------------------------ Rendern
function renderAll() {
  renderGroups();
  renderEvents(false);
  renderPersons();
  renderServices();
}

function renderGroups() {
  const list = $('groupsList');
  if (!state.groups.length) {
    list.replaceChildren(el('li', { class: 'empty' }, 'Keine passenden Gruppen.'));
    return;
  }
  list.replaceChildren(...state.groups.map((g) => el('li', {
    class: g.id === state.groupId ? 'selected' : '',
    onclick: () => selectGroup(g.id),
  }, g.name)));
}

function renderEvents(scrollToToday) {
  const list = $('eventsList');
  let firstUpcoming = null;
  const items = state.events.map((ev) => {
    const classes = [];
    if (ev.id === state.eventId) classes.push('selected');
    if (ev.past) classes.push('past');
    else if (!firstUpcoming) classes.push('today-marker');
    const li = el('li', { class: classes.join(' '), onclick: () => selectEvent(ev.id) }, ev.display);
    if (!ev.past && !firstUpcoming) firstUpcoming = li;
    return li;
  });
  list.replaceChildren(...(items.length ? items : [el('li', { class: 'empty' }, 'Keine Events im Zeitraum.')]));
  if (scrollToToday && firstUpcoming) firstUpcoming.scrollIntoView({ block: 'start' });
}

function renderPersons(loadingText) {
  const list = $('personsList');
  $('personsHint').textContent = state.eventId ? '' : '(Event wählen für Abwesenheiten/Regeln)';
  if (loadingText !== undefined) {
    list.replaceChildren(loadingText ? el('li', { class: 'loading' }, loadingText) : el('li', { class: 'empty' }, 'Gruppe wählen.'));
    return;
  }
  if (!state.groupId) { list.replaceChildren(el('li', { class: 'empty' }, 'Gruppe wählen.')); return; }
  if (!state.view || state.view.group_id !== state.groupId) return; // Ladeanzeige stehen lassen
  const persons = state.view.persons || [];
  if (!persons.length) { list.replaceChildren(el('li', { class: 'empty' }, 'Keine Personen.')); return; }
  list.replaceChildren(...persons.map((p) => {
    const unavailable = p.absent || p.rule_blocked;
    const li = el('li', {
      class: [unavailable ? 'unavailable' : '', p.id === state.selectedPersonId ? 'selected' : ''].join(' '),
      draggable: 'true',
      title: 'Auf einen Dienst ziehen oder anklicken und dann den Dienst anklicken. Rechtsklick für Optionen.',
      onclick: () => {
        state.selectedPersonId = state.selectedPersonId === p.id ? null : p.id;
        renderPersons();
        renderServices();
      },
      oncontextmenu: (e) => openContextMenu(e, p),
      ondragstart: (e) => {
        e.dataTransfer.setData('text/x-person-id', String(p.id));
        e.dataTransfer.effectAllowed = 'move';
      },
    },
    el('span', { class: 'name', title: p.rule.note || null }, p.name, p.rule.note ? ' ✎' : ''),
    p.absent && badge('abwesend', 'absent'),
    p.rule_blocked && badge(`Regel: ${p.rule_reason}`, 'absent'),
    !p.rule_blocked && p.rule_reason && badge(p.rule_reason),
    p.intern && badge('Praktikant', 'intern'),
    p.limit_reached && badge(`Limit ${p.month_count}/${p.rule.max_per_month}`, 'rule'),
    p.in_event.length > 0 && badge(`schon: ${p.in_event.join(', ')}`, 'pending'),
    el('span', { class: 'last', title: 'Letzter Einsatz in diesen Diensten' }, p.last_date ? fmtDate(p.last_date) : '–'));
    return li;
  }));
}

function personLine(person) {
  if (!person) return el('span', { class: 'muted' }, '—');
  return el('span', {},
    el('span', { class: person.external ? 'extern' : '' }, person.name),
    person.external && (person.concealed ? ' (verborgen)' : ' (extern)'),
    person.absent && ' ', person.absent && badge('abwesend', 'absent'),
    person.rule_reason && ' ', person.rule_reason && badge(`Regel: ${person.rule_reason}`, 'absent'),
    person.agreed === false && ' ', person.agreed === false && badge('angefragt', 'pending'));
}

function renderServices(loadingText) {
  const info = $('infoList');
  const list = $('servicesList');
  const view = state.view;
  const current = view && view.event_id === state.eventId ? view.event : null;
  $('eventTitle').textContent = current ? current.display : 'Dienste';
  $('setupBtn').hidden = !current;
  updateSaveBar();
  $('suggestBtn').hidden = true;

  if (loadingText !== undefined || !state.eventId) {
    const text = loadingText || 'Event wählen.';
    info.replaceChildren(el('li', { class: loadingText ? 'loading' : 'empty' }, text));
    list.replaceChildren();
    return;
  }
  if (!view || view.event_id !== state.eventId) return;

  info.replaceChildren(...view.info.map((r) => el('li', {},
    el('span', { class: 'label' }, r.label + (r.slot ? ` ${r.slot}` : '')), personLine(r.person))));

  if (!view.services.length) {
    list.replaceChildren(el('li', { class: 'empty' }, 'Für dieses Event sind keine planbaren Dienste vorhanden.'));
    return;
  }
  const byId = personsById();
  const selected = state.selectedPersonId != null ? byId.get(state.selectedPersonId) : null;
  const suggestions = activeSuggestions();
  $('suggestBtn').hidden = suggestions.size === 0;
  $('suggestBtn').textContent = suggestions.size > 1 ? `Alle ${suggestions.size} Vorschläge übernehmen` : 'Vorschlag übernehmen';
  list.replaceChildren(...view.services.map((row) => {
    const sug = suggestions.get(row.request_id);
    const change = state.pending.get(row.request_id);
    let person = row.person;
    if (change) {
      const p = change.person_id != null ? byId.get(change.person_id) : null;
      person = change.person_id == null ? null
        : { id: change.person_id, name: change.name, absent: !!(p && p.absent), external: false,
            rule_reason: p && p.rule_blocked ? p.rule_reason : null };
    }
    const li = el('li', {
      class: [change ? 'changed' : '', selected ? 'can-assign' : ''].join(' '),
      tabindex: '0',
      title: selected ? `Klicken, um ${selected.name} einzuteilen` : '',
      onclick: (e) => { if (selected && e.target.tagName !== 'BUTTON') assign(row, selected); },
      onkeydown: (e) => { if (e.key === 'Delete') unassign(row); },
      ondragover: (e) => { if (e.dataTransfer.types.includes('text/x-person-id')) { e.preventDefault(); li.classList.add('dragover'); } },
      ondragleave: () => li.classList.remove('dragover'),
      ondrop: (e) => {
        e.preventDefault();
        li.classList.remove('dragover');
        const p = byId.get(Number(e.dataTransfer.getData('text/x-person-id')));
        if (p) assign(row, p);
      },
    },
    el('span', { class: 'label' }, row.label + (row.slot ? ` ${row.slot}` : '')),
    el('span', { class: 'who' }, personLine(person),
      sug && el('span', { class: 'suggestion', title: sug.reason },
        ` Vorschlag: ${sug.name} (${sug.reason}) `,
        el('button', { class: 'small', onclick: () => applySuggestions([row.request_id]) }, 'übernehmen'))),
    el('button', { class: 'secondary small', disabled: !person, onclick: () => unassign(row) }, 'Entfernen'));
    return li;
  }));
}

// Vorschlaege, die noch passen: Dienst offen und unveraendert, Person nicht schon anderweitig vorgemerkt.
function activeSuggestions() {
  const view = state.view;
  if (!view || view.event_id !== state.eventId || view.group_id !== state.groupId) return new Map();
  const pendingPersons = new Set([...state.pending.values()].map((c) => c.person_id).filter((id) => id != null));
  const out = new Map();
  for (const row of view.services) {
    const sug = (view.suggestions || {})[String(row.request_id)];
    if (!sug || row.person || state.pending.has(row.request_id) || pendingPersons.has(sug.person_id)) continue;
    out.set(row.request_id, sug);
  }
  return out;
}

function applySuggestions(requestIds) {
  const sugs = activeSuggestions();
  for (const id of requestIds) {
    const sug = sugs.get(id);
    if (sug) state.pending.set(id, { person_id: sug.person_id, name: sug.name, expected: null });
  }
  renderServices();
}

function updateSaveBar() {
  const dirty = state.pending.size > 0;
  $('saveBtn').disabled = !dirty || state.saving || !state.eventId;
  $('discardBtn').disabled = !dirty || state.saving;
  if (dirty && !state.saving) setStatus('Änderungen nicht gespeichert.');
}

// ------------------------------------------------------------------ Planen
function assign(row, person) {
  if (!row.request_id) {
    alert('Dieser Dienst ist im Event nicht vorhanden und kann daher nicht geplant werden.');
    return;
  }
  if (person.absent) { alert('Diese Person ist am Event-Tag als abwesend eingetragen.'); return; }
  if (person.rule_blocked) {
    alert(`Regel: ${person.name} – ${person.rule_reason}.\nÄndern unter „Regeln“.`);
    return;
  }
  const internServices = (state.view && state.view.intern_services) || [];
  const isInternService = internServices.includes(row.label);
  if (person.intern && !isInternService) {
    alert(`${person.name} ist als Praktikant/in markiert und kann nur in Praktikanten-Dienste eingeteilt werden.`);
    return;
  }
  if (!person.intern && isInternService && !confirm(`${person.name} ist nicht als Praktikant/in markiert. Trotzdem in „${row.label}“ einteilen?`)) return;
  const already = person.in_event.filter((label) => !(row.person && row.person.id === person.id && label === row.label));
  if (already.length && !confirm(`${person.name} ist in diesem Event schon eingeteilt (${already.join(', ')}). Trotzdem einteilen?`)) return;
  if (person.limit_reached && !confirm(`${person.name} hat das Monatslimit erreicht (${person.month_count}/${person.rule.max_per_month}). Trotzdem einteilen?`)) return;
  if (row.person && row.person.id === person.id) state.pending.delete(row.request_id);
  else state.pending.set(row.request_id, { person_id: person.id, name: person.name, expected: row.person ? row.person.id : null });
  state.selectedPersonId = null;
  renderPersons();
  renderServices();
}

function unassign(row) {
  if (!row.request_id) return;
  if (!row.person) state.pending.delete(row.request_id);
  else state.pending.set(row.request_id, { person_id: null, expected: row.person.id });
  renderServices();
}

$('setupBtn').addEventListener('click', () => {
  const eventId = state.eventId;
  if (!eventId || state.saving) return;
  if (state.pending.size && !confirm('Es gibt nicht gespeicherte Einteilungen. Diese gehen dabei verloren. Weiter?')) return;
  window.openServiceSetup({
    api, eventId, groupId: state.groupId, title: $('eventTitle').textContent,
    onSaved: (res) => {
      state.pending.clear();
      if (state.eventId === eventId && res.view.group_id === state.groupId) {
        state.viewSeq++;
        applyView(res.view);
      }
      setStatus('Dienste in ChurchTools geändert.');
    },
  });
});

$('suggestBtn').addEventListener('click', () => applySuggestions([...activeSuggestions().keys()]));

$('discardBtn').addEventListener('click', () => {
  state.pending.clear();
  setStatus('');
  renderServices();
});

$('saveBtn').addEventListener('click', async () => {
  const eventId = state.eventId;
  if (!eventId || state.pending.size === 0) return;
  const changes = [...state.pending].map(([request_id, c]) => ({
    request_id, person_id: c.person_id, expected_person_id: c.expected ?? null,
  }));
  state.saving = true;
  setStatus('Speichern …');
  updateSaveBar();
  try {
    const res = await api(`/api/events/${eventId}/save`, { method: 'POST', body: { group_id: state.groupId, changes } });
    state.pending.clear();
    if (state.eventId === eventId && res.view.group_id === state.groupId) {
      state.viewSeq++; // laufende Ladevorgaenge sind damit ueberholt
      applyView(res.view);
    }
    setStatus(res.errors.length ? `Teilweise gespeichert (${res.saved}). Fehler: ${res.errors.join(' | ')}` : 'Gespeichert.');
    if (res.errors.length) alert(res.errors.join('\n'));
    refreshStats();
  } catch (err) {
    setStatus('Fehler beim Speichern.');
    alert(`Speichern fehlgeschlagen: ${err.message}`);
  } finally {
    state.saving = false;
    updateSaveBar();
  }
});

window.addEventListener('beforeunload', (e) => {
  if (state.pending.size) { e.preventDefault(); e.returnValue = ''; }
});

// ------------------------------------------------------------------ Kontextmenue
let menuPerson = null;

function openContextMenu(e, person) {
  e.preventDefault();
  menuPerson = person;
  const menu = $('contextMenu');
  const ev = state.view && state.view.event;
  const dayItem = menu.querySelector('[data-action=weekday]');
  dayItem.hidden = !ev || ev.weekday == null;
  if (ev && ev.weekday != null) {
    const blocked = person.rule.blocked_weekdays.includes(ev.weekday);
    dayItem.querySelector('.tick').textContent = blocked ? '✓' : '';
    dayItem.querySelector('.text').textContent = `Nie am ${ev.weekday_name} einplanen`;
  }
  menu.hidden = false;
  const x = Math.min(e.clientX, window.innerWidth - menu.offsetWidth - 8);
  const y = Math.min(e.clientY, window.innerHeight - menu.offsetHeight - 8);
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
}

document.addEventListener('click', () => { $('contextMenu').hidden = true; });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('contextMenu').hidden = true; });

$('contextMenu').addEventListener('click', async (e) => {
  const action = e.target.closest('li') && e.target.closest('li').dataset.action;
  const person = menuPerson;
  $('contextMenu').hidden = true;
  if (!person) return;
  if (action === 'copy') {
    try { await navigator.clipboard.writeText(String(person.id)); setStatus(`Person-Id ${person.id} kopiert.`); }
    catch (_) { window.prompt('Person-Id:', String(person.id)); }
  } else if (action === 'weekday') {
    const day = state.view.event.weekday;
    const rule = person.rule;
    const days = rule.blocked_weekdays.includes(day)
      ? rule.blocked_weekdays.filter((d) => d !== day) : [...rule.blocked_weekdays, day];
    await api(`/api/rules/${person.id}`, { method: 'PUT', body: { ...rule, blocked_weekdays: days } });
    refreshView();
  } else if (action === 'rules') {
    window.location.href = `/regeln#p${person.id}`;
  }
});

init().catch((err) => { document.body.prepend(el('p', { class: 'error' }, `Start fehlgeschlagen: ${err.message}`)); });
