/**
 * programs.js — Reifeprogramme verwalten
 *
 * API:
 *   GET    /api/programs           → Liste
 *   POST   /api/programs           → Neu anlegen
 *   PUT    /api/programs/<id>      → Aktualisieren
 *   DELETE /api/programs/<id>      → Löschen
 *   POST   /api/programs/<id>/activate → Starten
 *   POST   /api/programs/stop      → Stoppen
 *   GET    /api/programs/running   → Status laufendes Programm
 *   POST   /api/import_recipe      → JSON-LD Import
 */

document.addEventListener('DOMContentLoaded', () => {

    const msgContainer   = document.getElementById('message-container');
    const listContainer  = document.getElementById('programListContainer');
    const loadingMsg     = document.getElementById('loadingMessage');
    const sortSelect     = document.getElementById('sortSelect');

    const editModal      = document.getElementById('editProgramModal');
    const editForm       = document.getElementById('editProgramForm');
    const modalTitle     = document.getElementById('modalTitle');
    const editIdInput    = document.getElementById('editProgramId');
    const editNameInput  = document.getElementById('editProgramName');
    const editDescInput  = document.getElementById('editProgramDescription');
    const stepsList      = document.getElementById('editStepsList');
    const btnAddStep     = document.getElementById('btnAddStep');
    const btnCancelEdit  = document.getElementById('btnCancelEdit');

    const importModal    = document.getElementById('importModal');
    const importUrlInput = document.getElementById('importUrl');
    const btnDoImport    = document.getElementById('btnDoImport');
    const btnCancelImport= document.getElementById('btnCancelImport');

    document.getElementById('btnAddProgram').addEventListener('click', () => openModal());
    document.getElementById('btnImportProgram').addEventListener('click', () => importModal.classList.add('show-modal'));
    sortSelect.addEventListener('change', renderList);

    let allPrograms   = [];
    let runningPgmId  = null;
    let runningData   = null;
    let msgTimeout    = null;

    // ── Hilfsfunktionen ───────────────────────────────────────────────────────

    function showMsg(text, type = 'info', ms = 3500) {
        if (!msgContainer) return;
        msgContainer.className = 'message-container ' + type;
        msgContainer.textContent = text;
        msgContainer.classList.add('show');
        if (msgTimeout) clearTimeout(msgTimeout);
        msgTimeout = setTimeout(() => msgContainer.classList.remove('show'), ms);
    }

    async function apiFetch(url, opts = {}) {
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...opts,
        });
        return resp.json();
    }

    // ── Daten laden ───────────────────────────────────────────────────────────

    async function loadData() {
        try {
            const [pgms, running] = await Promise.all([
                apiFetch('/api/programs'),
                apiFetch('/api/programs/running'),
            ]);
            allPrograms  = Array.isArray(pgms) ? pgms : [];
            runningPgmId = running.active ? running.program_id : null;
            runningData  = running.active ? running : null;
            renderList();
        } catch (e) {
            loadingMsg.textContent = 'Fehler beim Laden.';
            showMsg('Fehler beim Laden der Programme.', 'error', 5000);
        }
    }

    // ── Darstellung ───────────────────────────────────────────────────────────

    function sortedPrograms() {
        const sorted = [...allPrograms];
        switch (sortSelect.value) {
            case 'name_asc':     return sorted.sort((a, b) => a.name.localeCompare(b.name));
            case 'name_desc':    return sorted.sort((a, b) => b.name.localeCompare(a.name));
            case 'created_asc':  return sorted.sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
            case 'created_desc': return sorted.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
            default:             return sorted;
        }
    }

    function renderList() {
        listContainer.innerHTML = '';
        if (allPrograms.length === 0) {
            listContainer.innerHTML = '<p>Keine Programme vorhanden. Klicke auf "Neues Programm".</p>';
            return;
        }
        sortedPrograms().forEach(pgm => listContainer.appendChild(buildCard(pgm)));
    }

    function buildCard(pgm) {
        const isRunning = pgm.id === runningPgmId;
        const card = document.createElement('div');
        card.className = 'program-card';
        if (isRunning) card.style.borderColor = '#00bcd4';

        const steps = pgm.steps || [];
        const totalH = steps.reduce((s, x) => s + (x.duration_h || 0), 0);
        const MODES_DISPLAY = { 0: 'nur Messen', 1: 'Voll-Auto', 2: 'nur Temp' };
        const stepSummary = steps.length === 0
            ? '<p class="no-steps-message">Keine Reifeschritte vorhanden.</p>'
            : steps.map((s, i) => {
                const modeLabel = MODES_DISPLAY[s.modes_id] ?? `Modus ${s.modes_id}`;
                return `<div class="step-item">
                    <p><strong>Schritt ${i + 1}:</strong>${s.description ? ' ' + escHtml(s.description) : ''}</p>
                    <p>Dauer: <span>${s.duration_h}h</span> | Temp: <span>${s.temp}°C</span> | Feuchte: <span>${s.moist}%</span> | Modus: <span>${modeLabel}</span></p>
                </div>`;
            }).join('');

        let runningBlock = '';
        if (isRunning && runningData) {
            const rd = runningData;
            const endDate = rd.step_ends_at
                ? new Date(rd.step_ends_at * 1000).toLocaleString('de-DE', {
                    day: '2-digit', month: '2-digit', year: 'numeric',
                    hour: '2-digit', minute: '2-digit' })
                : '—';
            const remH = rd.remaining_h != null ? Math.round(rd.remaining_h) + 'h' : '—';
            runningBlock = `
                <div class="running-status-block">
                    <div class="running-status-title">● Läuft — Schritt ${rd.current_step} / ${rd.total_steps}</div>
                    ${rd.step_description ? `<div class="running-status-desc">${escHtml(rd.step_description)}</div>` : ''}
                    <div class="running-status-vals">
                        Soll: <span>${rd.step_target_temp}°C / ${rd.step_target_hum}%rF</span>
                        &nbsp;|&nbsp; Verbleibend: <span>${remH}</span>
                        &nbsp;|&nbsp; Ende: <span>${endDate}</span>
                    </div>
                </div>`;
        }

        card.innerHTML = `
            <h3>${escHtml(pgm.name)}</h3>
            <div class="program-details">
                ${pgm.description ? `<p>${escHtml(pgm.description)}</p>` : ''}
                <p><strong>Schritte:</strong> ${steps.length} — Gesamtdauer: ${totalH}h (≈ ${(totalH / 24).toFixed(1)} Tage)</p>
            </div>
            ${runningBlock}
            <button class="toggle-steps-btn" data-id="${pgm.id}">Schritte anzeigen</button>
            <div class="program-steps-container" id="steps-${pgm.id}">
                ${stepSummary}
            </div>
            <div class="actions">
                <button class="edit-btn" data-id="${pgm.id}">Bearbeiten</button>
                ${isRunning
                    ? `<button class="stop-btn" data-id="${pgm.id}" style="background-color:#c0392b;color:#fff;">Stoppen</button>`
                    : `<button class="activate-btn" data-id="${pgm.id}">Starten</button>`}
                <button class="delete-btn" data-id="${pgm.id}" style="background-color:#dc3545;color:#fff;">Löschen</button>
            </div>`;

        card.querySelector('.toggle-steps-btn').addEventListener('click', e => {
            const container = document.getElementById('steps-' + e.target.dataset.id);
            const showing = container.classList.toggle('show');
            e.target.textContent = showing ? 'Schritte ausblenden' : 'Schritte anzeigen';
        });
        card.querySelector('.edit-btn').addEventListener('click',     () => openModal(pgm));
        card.querySelector('.delete-btn').addEventListener('click',   () => deleteProgram(pgm.id, pgm.name));
        const activateBtn = card.querySelector('.activate-btn');
        if (activateBtn) activateBtn.addEventListener('click', () => activateProgram(pgm.id, pgm.name));
        const stopBtn = card.querySelector('.stop-btn');
        if (stopBtn) stopBtn.addEventListener('click', stopProgram);

        return card;
    }

    function escHtml(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // ── Programm-Aktionen ─────────────────────────────────────────────────────

    async function activateProgram(id, name) {
        if (!confirm(`Reifeprogramm "${name}" starten?`)) return;
        try {
            const r = await apiFetch(`/api/programs/${id}/activate`, { method: 'POST' });
            if (r.ok) { showMsg(`Programm "${r.program_name}" gestartet.`, 'success'); loadData(); }
            else       showMsg('Fehler: ' + (r.error || ''), 'error', 5000);
        } catch (e) { showMsg('Verbindungsfehler.', 'error', 5000); }
    }

    async function stopProgram() {
        if (!confirm('Laufendes Programm wirklich stoppen?')) return;
        try {
            const r = await apiFetch('/api/programs/stop', { method: 'POST' });
            if (r.ok) { showMsg('Programm gestoppt.', 'success'); loadData(); }
            else       showMsg('Fehler: ' + (r.error || ''), 'error', 5000);
        } catch (e) { showMsg('Verbindungsfehler.', 'error', 5000); }
    }

    async function deleteProgram(id, name) {
        if (!confirm(`Programm "${name}" wirklich löschen?`)) return;
        try {
            const r = await apiFetch(`/api/programs/${id}`, { method: 'DELETE' });
            if (r.ok) { showMsg(`"${name}" gelöscht.`, 'success'); loadData(); }
            else       showMsg('Fehler: ' + (r.error || ''), 'error', 5000);
        } catch (e) { showMsg('Verbindungsfehler.', 'error', 5000); }
    }

    // ── Modal: Programm bearbeiten / anlegen ──────────────────────────────────

    const MODES = { 0: 'nur Messen', 1: 'Voll-Auto (Temp+Hum)', 2: 'nur Temp' };

    function openModal(pgm = null) {
        editIdInput.value   = pgm ? pgm.id : '';
        editNameInput.value = pgm ? pgm.name : '';
        editDescInput.value = pgm ? (pgm.description || '') : '';
        modalTitle.textContent = pgm ? 'Programm bearbeiten' : 'Neues Programm';
        stepsList.innerHTML = '';

        const steps = pgm ? (pgm.steps || []) : [{ duration_h: 48, temp: 14, moist: 80, description: '', modes_id: 1 }];
        steps.forEach(s => addStepRow(s));
        editModal.classList.add('show-modal');
        editNameInput.focus();
    }

    function addStepRow(step = {}) {
        const num = stepsList.querySelectorAll('.edit-step-item').length + 1;
        const div = document.createElement('div');
        div.className = 'edit-step-item';
        div.innerHTML = `
            <div class="step-number-title">Schritt ${num}</div>
            <div class="form-group-inline">
                <div class="input-wrapper">
                    <label>Dauer (h)</label>
                    <input type="number" class="step-duration" value="${step.duration_h || 48}" min="1" max="9999" step="1">
                </div>
                <div class="input-wrapper">
                    <label>Temperatur (°C)</label>
                    <input type="number" class="step-temp" value="${step.temp || 14}" min="-50" max="60" step="0.1">
                </div>
                <div class="input-wrapper">
                    <label>Luftfeuchtigkeit (%)</label>
                    <input type="number" class="step-moist" value="${step.moist || 80}" min="0" max="100" step="0.5">
                </div>
            </div>
            <div class="form-group-inline">
                <div class="input-wrapper" style="flex:2;">
                    <label>Beschreibung</label>
                    <input type="text" class="step-desc" value="${escHtml(step.description || '')}" placeholder="optional">
                </div>
                <div class="input-wrapper">
                    <label>Modus</label>
                    <select class="step-mode">
                        ${Object.entries(MODES).map(([v, l]) =>
                            `<option value="${v}"${parseInt(step.modes_id||1) === parseInt(v) ? ' selected' : ''}>${l}</option>`
                        ).join('')}
                    </select>
                </div>
            </div>
            <div class="step-actions">
                <button type="button" class="remove-step-btn">Schritt entfernen</button>
            </div>`;
        div.querySelector('.remove-step-btn').addEventListener('click', () => {
            div.remove();
            renumberSteps();
        });
        stepsList.appendChild(div);
    }

    function renumberSteps() {
        stepsList.querySelectorAll('.step-number-title').forEach((el, i) => {
            el.textContent = 'Schritt ' + (i + 1);
        });
    }

    btnAddStep.addEventListener('click', () => addStepRow());
    btnCancelEdit.addEventListener('click', () => editModal.classList.remove('show-modal'));

    editForm.addEventListener('submit', async e => {
        e.preventDefault();
        const id = editIdInput.value;
        const steps = [...stepsList.querySelectorAll('.edit-step-item')].map(row => ({
            duration_h:  parseInt(row.querySelector('.step-duration').value),
            temp:        parseFloat(row.querySelector('.step-temp').value),
            moist:       parseFloat(row.querySelector('.step-moist').value),
            description: row.querySelector('.step-desc').value.trim(),
            modes_id:    parseInt(row.querySelector('.step-mode').value),
        }));
        const payload = {
            name:        editNameInput.value.trim(),
            description: editDescInput.value.trim(),
            steps,
        };
        try {
            const method = id ? 'PUT' : 'POST';
            const url    = id ? `/api/programs/${id}` : '/api/programs';
            const r = await apiFetch(url, { method, body: JSON.stringify(payload) });
            if (r.id || r.name) {
                showMsg(`Programm "${r.name}" gespeichert.`, 'success');
                editModal.classList.remove('show-modal');
                loadData();
            } else {
                showMsg('Fehler: ' + (r.error || JSON.stringify(r.details || '')), 'error', 6000);
            }
        } catch (err) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    });

    // ── URL-Import ────────────────────────────────────────────────────────────

    btnCancelImport.addEventListener('click', () => importModal.classList.remove('show-modal'));
    btnDoImport.addEventListener('click', async () => {
        const url = importUrlInput.value.trim();
        if (!url) { showMsg('Bitte eine URL eingeben.', 'warning'); return; }
        showMsg('Importiere…', 'info', 10000);
        try {
            const r = await apiFetch('/api/import_recipe', { method: 'POST', body: JSON.stringify({ url }) });
            if (r.id || r.name) {
                showMsg(`Programm "${r.name}" importiert.`, 'success');
                importModal.classList.remove('show-modal');
                importUrlInput.value = '';
                loadData();
            } else {
                showMsg('Import-Fehler: ' + (r.error || '') + (r.details ? ' — ' + r.details.join(', ') : ''), 'error', 8000);
            }
        } catch (err) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    });

    // ── Start ─────────────────────────────────────────────────────────────────
    loadData();
});
