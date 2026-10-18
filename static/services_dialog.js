'use strict';

// Dialog "Dienste bearbeiten" – wie in ChurchTools "Dienst zum Event hinzufügen oder entfernen".
// Gemeinsam für Planung (app.js) und Monat (month.js). Nur geänderte Dienste werden geschickt.
// Aufruf: openServiceSetup({ api, eventId, title, groupId, onSaved(result) })

(function () {
  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k === 'checked' || k === 'value' || k === 'disabled') node[k] = v;
      else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  async function openServiceSetup({ api, eventId, title, groupId, onSaved }) {
    const status = h('span', { class: 'muted' }, 'Lade Dienste …');
    const body = h('tbody');
    const saveBtn = h('button', { type: 'button', disabled: true }, 'Speichern');
    const cancelBtn = h('button', { type: 'button', class: 'secondary' }, 'Abbrechen');
    const dialog = h('dialog', { class: 'svcdialog', 'aria-label': 'Dienste bearbeiten' },
      h('h2', {}, 'Dienste bearbeiten'),
      h('p', { class: 'muted small' }, title),
      h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Dienst'), h('th', {}, 'Anzahl'), h('th', {}, 'Eingeteilt'))), body),
      h('p', { class: 'muted small' }, 'Änderungen werden direkt in ChurchTools übernommen (gleiche Funktion wie der Dialog „Dienst zum Event hinzufügen oder entfernen“).'),
      h('div', { class: 'savebar' }, saveBtn, cancelBtn, status));
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    cancelBtn.addEventListener('click', close);
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    dialog.showModal();

    let rows = [];
    const counts = new Map(); // service_id -> gewünschte Anzahl

    function changes() {
      return rows.filter((r) => counts.get(r.service_id) !== r.count)
        .map((r) => ({ service_id: r.service_id, count: counts.get(r.service_id) }));
    }

    function render() {
      body.replaceChildren(...rows.map((r) => {
        const want = counts.get(r.service_id);
        const number = h('input', {
          type: 'number', min: '0', max: '20', class: 'num', value: want ? String(want) : '',
          'aria-label': `Anzahl ${r.name}`,
          onchange: (e) => { counts.set(r.service_id, Math.max(0, parseInt(e.target.value, 10) || 0)); render(); },
        });
        return h('tr', { class: want !== r.count ? 'changed' : '' },
          h('td', {}, h('label', { class: 'check' },
            h('input', {
              type: 'checkbox', checked: want > 0,
              onchange: (e) => { counts.set(r.service_id, e.target.checked ? Math.max(1, r.count) : 0); render(); },
            }), ` ${r.name}`)),
          h('td', {}, number),
          h('td', { class: 'muted small' }, r.assigned.join(', ') || '—'));
      }));
      const n = changes().length;
      saveBtn.disabled = n === 0;
      status.textContent = n ? `${n} Änderung(en)` : '';
    }

    try {
      const res = await api(`/api/events/${eventId}/servicesetup`);
      rows = res.services;
      for (const r of rows) counts.set(r.service_id, r.count);
      render();
    } catch (err) {
      status.textContent = `Fehler: ${err.message}`;
      return;
    }

    saveBtn.addEventListener('click', async () => {
      const list = changes();
      // Warnung, wenn dadurch eingeteilte Personen ihren Dienst verlieren können
      const risky = rows.filter((r) => {
        const want = counts.get(r.service_id);
        return want < r.count && r.assigned.length > want;
      });
      if (risky.length && !confirm(`Achtung: Bei ${risky.map((r) => `${r.name} (${r.assigned.join(', ')})`).join('; ')} `
        + 'sind Personen eingeteilt. Wird der Dienst entfernt oder verkleinert, kann ihre Einteilung wegfallen. Trotzdem speichern?')) return;
      saveBtn.disabled = true;
      status.textContent = 'Speichern …';
      try {
        const res = await api(`/api/events/${eventId}/servicesetup`, { method: 'POST', body: { group_id: groupId, changes: list } });
        close();
        if (onSaved) onSaved(res);
      } catch (err) {
        status.textContent = `Fehler: ${err.message}`;
        saveBtn.disabled = false;
      }
    });
  }

  window.openServiceSetup = openServiceSetup;
})();
