/**
 * settings.js — Einstellungen lesen und speichern
 *
 * API:
 *   GET  /api/settings              → Alle Einstellungen
 *   POST /api/settings              → Speichern
 *   POST /api/settings/test_email   → Test-E-Mail
 *   POST /api/settings/test_ntfy    → Test-ntfy
 *   GET  /api/archive/count         → DB-Infos
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
        cfgTargetTemp:      'target_temp',
        cfgTargetHum:       'target_hum',
        cfgHystCool:        'temp_hyst_cool',
        cfgHystHeat:        'temp_hyst_heat',
        cfgHystMois:        'hum_hyst_mois',
        cfgHystFana:        'hum_hyst_fana',
        cfgRefreshrate:     'refreshrate',
        cfgArchHandling:    'arch_handling',
        cfgAhtHumidify:     'aht21_hyst_humidify',
        cfgAhtProtect:      'aht21_hyst_protect',
        cfgAbsHumThreshold: 'abs_hum_threshold',
        cfgGpioFanu:        'gpio_pin_fanu',
        cfgGpioCool:        'gpio_pin_cool',
        cfgGpioFana:        'gpio_pin_fana',
        cfgGpioMois:        'gpio_pin_mois',
        cfgGpioHeat:        'gpio_pin_heat',
        cfgMailSmtp:        'mail_smtp',
        cfgMailPort:        'mail_port',
        cfgMailUser:        'mail_user',
        cfgMailPassword:    'mail_password',
        cfgMailReceiver:    'mail_receiver',
        cfgEmailSleep:      'email_sleep',
        cfgNtfyServer:      'ntfy_server',
        cfgNtfyTopic:       'ntfy_topic',
        cfgNtfyUser:        'ntfy_user',
        cfgNtfyToken:       'ntfy_token',
    };

    // Bool-Toggle-Felder
    const TOGGLES = {
        cfgMailEnabled:      'mail_enabled',
        cfgNtfyEnabled:      'ntfy_enabled',
        cfgRelayActiveHigh:  'relay_active_high',
    };

    function updateRelayActiveHighLabel() {
        const cb  = document.getElementById('cfgRelayActiveHigh');
        const lbl = document.getElementById('lblRelayActiveHigh');
        if (cb && lbl) lbl.textContent = cb.checked ? 'Active-High' : 'Active-Low';
    }

    // ── Einstellungen laden ───────────────────────────────────────────────────

    async function loadSettings() {
        try {
            const resp = await fetch('/api/settings');
            if (!resp.ok) { showMsg('Einstellungen konnten nicht geladen werden.', 'error'); return; }
            const cfg = await resp.json();

            Object.entries(FIELDS).forEach(([domId, apiKey]) => {
                const el = document.getElementById(domId);
                if (el && cfg[apiKey] !== undefined) {
                    el.value = cfg[apiKey];
                }
            });
            Object.entries(TOGGLES).forEach(([domId, apiKey]) => {
                const el = document.getElementById(domId);
                if (el) el.checked = !!cfg[apiKey];
            });
            updateRelayActiveHighLabel();

            // Passwort-/Token-Felder nie vorausfüllen
            const pwEl = document.getElementById('cfgMailPassword');
            if (pwEl) pwEl.value = '';
            const tokenEl = document.getElementById('cfgNtfyToken');
            if (tokenEl) tokenEl.value = '';

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

    async function saveSettings() {
        const payload = {};

        Object.entries(FIELDS).forEach(([domId, apiKey]) => {
            const el = document.getElementById(domId);
            if (!el) return;
            const val = el.value.trim();
            if (domId === 'cfgMailPassword' && val === '') return; // leer = unverändert
            if (domId === 'cfgNtfyToken'    && val === '') return; // leer = unverändert
            if (val !== '') payload[apiKey] = val;
        });
        Object.entries(TOGGLES).forEach(([domId, apiKey]) => {
            const el = document.getElementById(domId);
            if (el) payload[apiKey] = el.checked ? 1 : 0;
        });

        const resp = await fetch('/api/settings', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        return resp.json();
    }

    document.getElementById('btnSaveSettings').addEventListener('click', async () => {
        try {
            const r = await saveSettings();
            if (r.ok) {
                const msg = r.warning
                    ? 'Gespeichert. ⚠ ' + r.warning
                    : 'Einstellungen gespeichert.';
                showMsg(msg, r.warning ? 'warning' : 'success', 6000);
            } else {
                const details = r.details ? r.details.join(' | ') : '';
                showMsg('Fehler: ' + (r.error || '') + (details ? ' — ' + details : ''), 'error', 8000);
            }
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error');
        }
    });

    // ── Test-E-Mail ───────────────────────────────────────────────────────────

    document.getElementById('btnTestEmail').addEventListener('click', async () => {
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
            if (!saved.ok) {
                const details = saved.details ? saved.details.join(' | ') : '';
                showMsg('Speichern fehlgeschlagen: ' + (saved.error || '') + (details ? ' — ' + details : ''), 'error', 8000);
                return;
            }
            showMsg('Gespeichert — sende ntfy-Test…', 'info', 10000);
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

    // ── Außensensor-Panel ein-/ausblenden ─────────────────────────────────────

    async function updateExternPanel() {
        try {
            const resp = await fetch('/api/hardware_setup');
            if (!resp.ok) return;
            const hw = await resp.json();
            const panel = document.getElementById('panelExternSensor');
            if (panel) {
                const hasExtern = hw.sensor_extern && hw.sensor_extern !== 'none';
                panel.style.display = hasExtern ? '' : 'none';
            }
        } catch (e) { /* nicht kritisch */ }
    }

    document.getElementById('cfgRelayActiveHigh')
        ?.addEventListener('change', updateRelayActiveHighLabel);

    // ── Init ──────────────────────────────────────────────────────────────────
    loadSettings();
    loadDbInfo();
    updateExternPanel();
});
