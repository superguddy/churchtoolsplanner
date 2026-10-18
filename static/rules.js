'use strict';

// Regel-Seite: gesperrte Wochentage, Monatslimit, Notiz und Ausblenden je Gruppe.
// Jede Aenderung wird sofort gespeichert (PUT /api/rules/{id} bzw. POST /api/hidden).

const $ = (id) => document.getElementById(id);
let data = null;

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
    else if (k === 'checked' || k === 'value') node[k] = v;
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function setSaveState(text, isError = false) {
  const s = $('saveState');
  s.textContent = text;
  s.className = isError ? 'error' : 'muted';
}

async function saveRule(person, rerender = true) {
  setSaveState('Speichern …');
  try {
    const res = await api(`/api/rules/${person.id}`, { method: 'PUT', body: person.rule });
    person.rule = res.rule;
    setSaveState(`Gespeichert: ${person.name}`);
  } catch (err) {
    setSaveState(`Fehler: ${err.message}`, true);
  }
  if (rerender) render(); // Textfelder nicht neu aufbauen, sonst springt der Fokus weg
}

async function saveHidden(person, groupId, hidden) {
  setSaveState('Speichern …');
  try {
    await api('/api/hidden', { method: 'POST', body: { group_id: groupId, person_id: person.id, hidden } });
    person.hidden_in = hidden ? [...person.hidden_in, groupId] : person.hidden_in.filter((g) => g !== groupId);
    setSaveState(`Gespeichert: ${person.name}`);
  } catch (err) {
    setSaveState(`Fehler: ${err.message}`, true);
  }
}

function hasRule(p) {
  return p.rule.blocked_weekdays.length || p.rule.max_per_month || p.rule.note || p.hidden_in.length;
}

function render() {
  const groupId = Number($('groupFilter').value) || null;
  const q = $('search').value.trim().toLowerCase();
  const onlyRules = $('onlyRules').checked;
  const groupName = new Map(data.groups.map((g) => [g.id, g.name]));
  const rows = data.persons.filter((p) =>
    (!groupId || p.groups.includes(groupId)) && (!q || p.name.toLowerCase().includes(q)) && (!onlyRules || hasRule(p)));

  if (!rows.length) {
    $('rulesBody').replaceChildren(el('tr', {}, el('td', { colspan: '5', class: 'empty' }, 'Keine Personen gefunden.')));
    return;
  }
  $('rulesBody').replaceChildren(...rows.map((p) => {
    const days = el('div', { class: 'daytoggles' }, ...data.weekdays.map((name, d) => {
      const on = p.rule.blocked_weekdays.includes(d);
      return el('button', {
        type: 'button',
        class: `day ${on ? 'on' : ''}`,
        title: on ? `${name}: nie einplanen (klicken zum Aufheben)` : `${name}: erlaubt (klicken, um zu sperren)`,
        'aria-pressed': on ? 'true' : 'false',
        onclick: () => {
          p.rule.blocked_weekdays = on ? p.rule.blocked_weekdays.filter((x) => x !== d) : [...p.rule.blocked_weekdays, d];
          saveRule(p);
        },
      }, name);
    }), p.rule.summary ? el('span', { class: 'muted small' }, p.rule.summary) : null);

    const max = el('input', {
      type: 'number', min: '0', max: '31', class: 'num', value: p.rule.max_per_month || '',
      onchange: (e) => { const v = parseInt(e.target.value, 10); p.rule.max_per_month = v > 0 ? v : null; saveRule(p, false); },
    });
    const note = el('input', {
      type: 'text', class: 'note', maxlength: '500', value: p.rule.note || '', placeholder: '—',
      onchange: (e) => { p.rule.note = e.target.value; saveRule(p, false); },
    });
    const hidden = el('div', { class: 'hiddenin' }, ...p.groups.map((gid) => el('label', { class: 'check' },
      el('input', { type: 'checkbox', checked: p.hidden_in.includes(gid), onchange: (e) => saveHidden(p, gid, e.target.checked) }),
      ` ${groupName.get(gid) || gid}`)));

    return el('tr', { id: `p${p.id}`, class: hasRule(p) ? 'hasrule' : '' },
      el('td', {}, p.name, el('div', { class: 'muted small' }, `ID ${p.id}`)),
      el('td', {}, days), el('td', {}, max), el('td', {}, note), el('td', {}, hidden));
  }));
}

async function init() {
  const s = await api('/api/session');
  if (!s.logged_in) { window.location.href = '/'; return; }
  $('userInfo').textContent = `${s.username} · ${s.base_url}`;
  data = await api('/api/rules');
  $('groupFilter').append(...data.groups.map((g) => el('option', { value: String(g.id) }, g.name)));
  for (const id of ['groupFilter', 'search', 'onlyRules']) $(id).addEventListener('input', render);
  render();
  const target = window.location.hash && document.querySelector(window.location.hash);
  if (target) { target.classList.add('focus'); target.scrollIntoView({ block: 'center' }); }
}

init().catch((err) => setSaveState(`Fehler: ${err.message}`, true));
