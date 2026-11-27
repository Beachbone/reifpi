/**
 * settings.js — Einstellungen lesen und speichern
 *
 * API:
 *   GET  /api/settings              → Alle Einstellungen
 *   POST /api/settings              → Speichern (nur geänderte Felder)
 *   POST /api/settings/test_email   → Test-E-Mail
 *   POST /api/settings/test_ntfy    → Test-ntfy
 *   GET  /api/archive/count         → DB-Infos
 *   GET  /api/hardware_setup        → Außensensor vorhanden?
 */

document.addEventListener('DOMContentLoaded', () => {

    const msgContainer = document.getElementById('message-container');
    let msgTimeout     = null;

    function showMsg(text, type = 'info', ms = 4000) {
        if (!msgContainer) return;
        msgContainer.className = 'message-container ' + type;
        msgContainer.textContent = text;
        msgContainer.classList.add('show');
        if (msgTimeout) clearTimeout(msgTimeout);
        msgTimeout = setTimeout(() => msgContainer.classList.remove('show'), ms);
    }

    // ── Mapping: DOM-ID → API-Feldname ────────────────────────────────────────
    const FIELDS = {
        cfgHystCool:          'temp_hyst_cool',
        cfgHystHeat:          'temp_hyst_heat',
        cfgHystMois:          'hum_hyst_mois',
        cfgHystFana:          'hum_hyst_fana',
        cfgOffsetCool:        'temp_offset_cool',
        cfgOffsetHeat:        'temp_offset_heat',
        cfgOffsetMois:        'hum_offset_mois',
        cfgLeadCool:          'lead_secs_cool',
        cfgLeadHeat:          'lead_secs_heat',
        cfgLeadMois:          'lead_secs_mois',
        cfgMinOffCool:        'min_off_secs_cool',
        cfgMinOffHeat:        'min_off_secs_heat',
        cfgMinOffMois:        'min_off_secs_mois',
        cfgLockHeatAfterCool: 'lock_secs_heat_after_cool',
        cfgLockCoolAfterHeat: 'lock_secs_cool_after_heat',
        cfgMoisDryCheck:      'mois_dry_check_min',
        cfgMoisDryRise:       'mois_dry_min_rise',
        cfgProbeInterval:     'mois_probe_interval_h',
        cfgProbeSecs:         'mois_probe_secs',
        cfgProbeRise:         'mois_probe_min_rise',
        cfgAirInterval:       'air_interval_h',
        cfgAirDuration:       'air_duration_min',
        cfgAirShort:          'air_short_pct',
        cfgAirAbortTemp:      'air_abort_temp_k',
        cfgAirAbortHum:       'air_abort_hum_pct',
        cfgRefreshrate:       'refreshrate',
        cfgArchHandling:      'arch_handling',
        cfgAhtHumidify:       'aht21_hyst_humidify',
        cfgAhtProtect:        'aht21_hyst_protect',
        cfgAbsHumThreshold:   'abs_hum_threshold',
        cfgGpioFanu:          'gpio_pin_fanu',
        cfgGpioCool:          'gpio_pin_cool',
        cfgGpioFana:          'gpio_pin_fana',
        cfgGpioMois:          'gpio_pin_mois',
        cfgGpioHeat:          'gpio_pin_heat',
        cfgMailSmtp:          'mail_smtp',
        cfgMailPort:          'mail_port',
        cfgMailUser:          'mail_user',
        cfgMailPassword:      'mail_password',
        cfgMailReceiver:      'mail_receiver',
        cfgEmailSleep:        'email_sleep',
        cfgNtfyServer:        'ntfy_server',
        cfgNtfyTopic:         'ntfy_topic',
        cfgNtfyUser:          'ntfy_user',
        cfgNtfyToken:         'ntfy_token',
    };

    // Bool-Toggle-Felder
    const TOGGLES = {
        cfgMailEnabled:      'mail_enabled',
        cfgNtfyEnabled:      'ntfy_enabled',
        cfgRelayActiveHigh:  'relay_active_high',
    };

    // Werden nie vorausgefüllt; leer = unverändert
    const SECRET_FIELDS = new Set(['cfgMailPassword', 'cfgNtfyToken']);

    const saveBtn   = document.getElementById('btnSaveSettings');
    const resetBtn  = document.getElementById('btnDiscardSettings');
    const dirtyInfo = document.getElementById('dirtyInfo');

    // Zuletzt geladene Werte (Vergleichsbasis für "geändert")
    let loaded = {};

    // ── Reiter ────────────────────────────────────────────────────────────────

    const TAB_KEY = 'reifpi.settingsTab';
    const tabs    = Array.from(document.querySelectorAll('.sp-tabs [role="tab"]'));

    function selectTab(tab, focus = false) {
        tabs.forEach(t => {
            const on = t === tab;
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            t.tabIndex = on ? 0 : -1;
            document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
        });
        if (focus) tab.focus();
        try { localStorage.setItem(TAB_KEY, tab.id); } catch (e) { /* ohne Speicher ok */ }
    }

    tabs.forEach((tab, i) => {
        tab.addEventListener('click', () => selectTab(tab));
        tab.addEventListener('keydown', e => {
            let next = null;
            if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
            if (e.key === 'ArrowLeft')  next = tabs[(i - 1 + tabs.length) % tabs.length];
            if (e.key === 'Home')       next = tabs[0];
            if (e.key === 'End')        next = tabs[tabs.length - 1];
            if (next) { e.preventDefault(); selectTab(next, true); }
        });
    });

    try {
        const saved = document.getElementById(localStorage.getItem(TAB_KEY) || '');
        if (saved && tabs.includes(saved)) selectTab(saved);
    } catch (e) { /* ohne Speicher ok */ }

    function showTabOf(el) {
        const panel = el.closest('[role="tabpanel"]');
        const tab   = panel && tabs.find(t => t.getAttribute('aria-controls') === panel.id);
        if (tab) selectTab(tab);
    }

    // ── −/+-Tasten ────────────────────────────────────────────────────────────

    function decimals(step) {
        const s = String(step);
        return s.includes('.') ? s.split('.')[1].length : 0;
    }

    function stepInput(inp, dir) {
        const step = parseFloat(inp.step) || 1;
        const min  = inp.min !== '' ? parseFloat(inp.min) : -Infinity;
        const max  = inp.max !== '' ? parseFloat(inp.max) :  Infinity;
        let v = parseFloat(inp.value);
        if (isNaN(v)) v = Math.max(min, 0);
        // Auf das Raster einrasten, dann einen Schritt weiter
        v = Math.round(v / step) * step + dir * step;
        v = Math.min(max, Math.max(min, v));
        inp.value = v.toFixed(decimals(inp.step || 1));
        inp.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function makeStepButton(inp, dir, labelText) {
        const btn = document.createElement('button');
        btn.type      = 'button';
        btn.className = 'sp-step ' + (dir < 0 ? 'minus' : 'plus');
        btn.textContent = dir < 0 ? '−' : '+';
        btn.setAttribute('aria-label', labelText + (dir < 0 ? ' verringern' : ' erhöhen'));

        // Ein Schritt erst beim vollständigen Tippen (click): Wer beim Scrollen
        // auf einer Taste landet, verstellt nichts — der Browser bricht den
        // Tipp dann mit pointercancel ab. Gedrückt halten (≥ 500 ms ohne
        // Bewegung) zählt danach schnell weiter.
        let delay = null, repeat = null, held = false, startX = 0, startY = 0;
        const stop = () => { clearTimeout(delay); clearInterval(repeat); delay = repeat = null; };
        btn.addEventListener('pointerdown', e => {
            if (e.button !== 0) return;
            held   = false;
            startX = e.clientX;
            startY = e.clientY;
            delay  = setTimeout(() => {
                held = true;
                stepInput(inp, dir);
                repeat = setInterval(() => stepInput(inp, dir), 90);
            }, 500);
        });
        btn.addEventListener('pointermove', e => {
            if (delay && Math.hypot(e.clientX - startX, e.clientY - startY) > 8) stop();
        });
        ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev => btn.addEventListener(ev, stop));
        btn.addEventListener('click', () => {
            if (held) { held = false; return; }   // Halten hat schon gezählt
            stepInput(inp, dir);
        });
        btn.addEventListener('contextmenu', e => e.preventDefault());
        return btn;
    }

    // Feldname mit Karte, z.B. "Kühlen Vorhaltzeit" (für Tasten und Meldungen)
    function fieldName(inp) {
        const label = document.querySelector(`label[for="${inp.id}"]`);
        const name  = label ? label.firstChild.textContent.trim() : inp.id;
        const card  = inp.closest('.sp-card');
        const title = card ? card.querySelector('h2').textContent.trim() : '';
        return title && title !== name ? `${title} ${name}` : name;
    }

    document.querySelectorAll('input[data-stepper]').forEach(inp => {
        const text = fieldName(inp);

        const wrap = document.createElement('div');
        wrap.className = 'sp-stepper';
        inp.parentNode.insertBefore(wrap, inp);
        wrap.append(makeStepButton(inp, -1, text), inp, makeStepButton(inp, 1, text));
    });

    // ── Änderungen verfolgen ──────────────────────────────────────────────────

    function currentValue(domId) {
        const el = document.getElementById(domId);
        if (!el) return undefined;
        if (domId in TOGGLES) return el.checked ? 1 : 0;
        return el.value.trim();
    }

    function sameValue(domId, now) {
        const was = loaded[domId];
        if (SECRET_FIELDS.has(domId)) return now === '';
        const el = document.getElementById(domId);
        if (el && el.type === 'number') return parseFloat(now) === parseFloat(was);
        return String(now) === String(was ?? '');
    }

    function changedFields() {
        const changed = {};
        [...Object.keys(FIELDS), ...Object.keys(TOGGLES)].forEach(domId => {
            const now = currentValue(domId);
            if (now === undefined || sameValue(domId, now)) return;
            changed[domId] = now;
        });
        return changed;
    }

    function updateDirty() {
        const changed = changedFields();
        const n = Object.keys(changed).length;
        document.querySelectorAll('.sp-field, .sp-toggle').forEach(f => f.classList.remove('is-dirty'));
        Object.keys(changed).forEach(domId => {
            document.getElementById(domId)?.closest('.sp-field, .sp-toggle')?.classList.add('is-dirty');
        });
        saveBtn.disabled  = n === 0;
        resetBtn.disabled = n === 0;
        dirtyInfo.textContent = n === 0 ? 'Keine Änderungen'
            : n === 1 ? '1 ungespeicherte Änderung'
            : `${n} ungespeicherte Änderungen`;
        dirtyInfo.classList.toggle('has-changes', n > 0);
        return n;
    }

    document.querySelector('main').addEventListener('input',  updateDirty);
    document.querySelector('main').addEventListener('change', updateDirty);

    window.addEventListener('beforeunload', e => {
        if (Object.keys(changedFields()).length) { e.preventDefault(); e.returnValue = ''; }
    });

    function updateRelayActiveHighLabel() {
        const cb  = document.getElementById('cfgRelayActiveHigh');
        const lbl = document.getElementById('lblRelayActiveHigh');
        if (cb && lbl) lbl.textContent = cb.checked ? 'HIGH (active-high)' : 'LOW (active-low)';
    }
    document.getElementById('cfgRelayActiveHigh')
        ?.addEventListener('change', updateRelayActiveHighLabel);

    // ── Einstellungen laden ───────────────────────────────────────────────────

    async function loadSettings() {
        try {
            const resp = await fetch('/api/settings');
            if (!resp.ok) { showMsg('Einstellungen konnten nicht geladen werden.', 'error'); return; }
            const cfg = await resp.json();

            loaded = {};
            Object.entries(FIELDS).forEach(([domId, apiKey]) => {
                const el = document.getElementById(domId);
                if (!el) return;
                if (SECRET_FIELDS.has(domId)) { el.value = ''; return; }
                const v = cfg[apiKey];
                el.value = (v === undefined || v === null) ? ''
                    : el.hasAttribute('data-stepper') ? Number(v).toFixed(decimals(el.step || 1))
                    : v;
                loaded[domId] = el.value;
            });
            Object.entries(TOGGLES).forEach(([domId, apiKey]) => {
                const el = document.getElementById(domId);
                if (!el) return;
                el.checked = !!cfg[apiKey];
                loaded[domId] = el.checked ? 1 : 0;
            });
            updateRelayActiveHighLabel();
            updateDirty();
        } catch (e) {
            showMsg('Verbindungsfehler beim Laden.', 'error');
        }
    }

    // ── DB-Infos laden ────────────────────────────────────────────────────────

    async function loadDbInfo() {
        try {
            const resp = await fetch('/api/archive/count');
            if (!resp.ok) return;
            const info = await resp.json();

            const fmt = ts => ts ? new Date(ts * 1000).toLocaleString('de-DE') : '—';
            document.getElementById('dbTotal').textContent   = info.total    ?? '—';
            document.getElementById('dbLast24h').textContent = info.last_24h ?? '—';
            document.getElementById('dbOldest').textContent  = fmt(info.oldest);
            document.getElementById('dbNewest').textContent  = fmt(info.newest);
        } catch (e) {
            // DB-Info ist nicht kritisch
        }
    }

    // ── Einstellungen speichern ───────────────────────────────────────────────

    // Gibt {ok:true, nothing:true} zurück, wenn nichts geändert war
    async function saveSettings() {
        const changed = changedFields();
        if (!Object.keys(changed).length) return { ok: true, nothing: true };

        // Ungültige Eingaben vorher abfangen (Reiter zeigen, Feld fokussieren)
        for (const domId of Object.keys(changed)) {
            const el = document.getElementById(domId);
            if (el && !el.checkValidity()) {
                showTabOf(el);
                el.focus();
                const range = el.min !== '' && el.max !== '' ? ` (erlaubt ${el.min} bis ${el.max})` : '';
                return { ok: false, error: `Ungültiger Wert bei „${fieldName(el)}“${range}` };
            }
        }

        const payload = {};
        Object.entries(changed).forEach(([domId, val]) => {
            payload[FIELDS[domId] || TOGGLES[domId]] = val;
        });

        const resp = await fetch('/api/settings', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const r = await resp.json();
        if (r.ok) {
            await loadSettings();
            // Der Termin entsteht erst im nächsten Regelzyklus
            setTimeout(updateAirStatus, 1500);
            setTimeout(updateAirStatus, 12000);
        }
        return r;
    }

    function showSaveResult(r) {
        if (r.ok) {
            const msg = r.warning ? 'Gespeichert. ⚠ ' + r.warning : 'Einstellungen gespeichert.';
            showMsg(msg, r.warning ? 'warning' : 'success', 6000);
        } else {
            const details = r.details ? r.details.join(' | ') : '';
            showMsg('Fehler: ' + (r.error || '') + (details ? ' — ' + details : ''), 'error', 8000);
        }
    }

    saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        try {
            showSaveResult(await saveSettings());
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error');
        }
        updateDirty();
    });

    // ── Änderungen verwerfen ──────────────────────────────────────────────────

    resetBtn.addEventListener('click', async () => {
        await loadSettings();   // Werte vom Pi neu laden, Markierungen weg
        showMsg('Änderungen verworfen.', 'info', 3000);
    });

    // ── Test-E-Mail ───────────────────────────────────────────────────────────

    document.getElementById('btnTestEmail').addEventListener('click', async () => {
        if (updateDirty()) {
            showMsg('Bitte zuerst speichern — der Test nutzt die gespeicherten Werte.', 'warning', 6000);
            return;
        }
        showMsg('Sende Test-E-Mail…', 'info', 10000);
        try {
            const resp = await fetch('/api/settings/test_email', { method: 'POST' });
            const r    = await resp.json();
            if (r.ok) showMsg(r.message || 'Test-E-Mail gesendet.', 'success');
            else       showMsg('Fehler: ' + (r.error || ''), 'error', 6000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error');
        }
    });

    // ── Setup speichern + Test-ntfy ───────────────────────────────────────────

    document.getElementById('btnTestNtfy').addEventListener('click', async () => {
        showMsg('Einstellungen speichern…', 'info', 10000);
        try {
            const saved = await saveSettings();
            updateDirty();
            if (!saved.ok) { showSaveResult(saved); return; }
            showMsg('Sende ntfy-Test…', 'info', 10000);
            const resp = await fetch('/api/settings/test_ntfy', { method: 'POST' });
            const r    = await resp.json();
            if (r.ok) showMsg(r.message || 'ntfy-Nachricht gesendet.', 'success');
            else       showMsg('Fehler: ' + (r.error || ''), 'error', 6000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error');
        }
    });

    // ── Hardware Setup ────────────────────────────────────────────────────────

    document.getElementById('btnHardwareSetup').addEventListener('click', () => {
        const ok = confirm(
            'Hardware Setup öffnen?\n\n' +
            'Diese Seite ist für die einmalige Sensor-Konfiguration.\n' +
            'Änderungen erfordern einen Service-Neustart.'
        );
        if (ok) window.location.href = '/hardware_setup';
    });

    // ── Status Luftwechsel ────────────────────────────────────────────────────

    async function updateAirStatus() {
        const el = document.getElementById('airStatus');
        if (!el) return;
        try {
            const resp = await fetch('/api/status');
            if (!resp.ok) return;
            const st  = await resp.json();
            const fmt = ts => new Date(ts * 1000).toLocaleString('de-DE', {
                weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
            });
            let text;
            if (!st.air_interval_h) {
                text = 'Aus';
            } else if (st.air_active) {
                text = `Läuft${st.air_short ? ' verkürzt' : ''} bis ${fmt(st.air_end)}`;
            } else if (st.air_next_due) {
                text = `Nächster: ${fmt(st.air_next_due)}`;
            } else {
                text = 'Aktiv – Termin wird berechnet …';
            }
            if (st.air_last_result) text += ` · Zuletzt: ${st.air_last_result}`;
            el.textContent = text;

            const pe = document.getElementById('probeStatus');
            if (pe) {
                let p;
                if (!st.mois_probe_interval_h)  p = 'Aus';
                else if (st.mois_fault)         p = 'Befeuchter gesperrt – Test nach Freigabe';
                else if (st.mois_probe_active)  p = 'Test läuft …';
                else if (st.mois_probe_next_due) p = `Nächster: ab ${fmt(st.mois_probe_next_due)}, sobald ruhig`;
                else                             p = 'Aktiv – Termin wird berechnet …';
                if (st.mois_probe_result) p += ` · Zuletzt: ${st.mois_probe_result}`;
                pe.textContent = p;
            }
        } catch (e) { /* nicht kritisch */ }
    }

    // ── Außensensor-Karte ein-/ausblenden ─────────────────────────────────────

    async function updateExternPanel() {
        try {
            const resp = await fetch('/api/hardware_setup');
            if (!resp.ok) return;
            const hw = await resp.json();
            const panel = document.getElementById('panelExternSensor');
            if (panel) {
                const hasExtern = hw.sensor_extern && hw.sensor_extern !== 'none';
                panel.hidden = !hasExtern;
            }
        } catch (e) { /* nicht kritisch */ }
    }

    // ── Init ──────────────────────────────────────────────────────────────────
    loadSettings();
    loadDbInfo();
    updateExternPanel();
    updateAirStatus();
    setInterval(updateAirStatus, 30000);
});
