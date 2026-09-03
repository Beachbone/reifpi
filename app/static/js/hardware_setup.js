/**
 * hardware_setup.js — Hardware-Sensor-Konfiguration
 *
 * API:
 *   GET  /api/hardware_setup
 *   POST /api/hardware_setup
 */

document.addEventListener('DOMContentLoaded', () => {

    // ── Hilfsfunktionen ───────────────────────────────────────────────────────

    function showMessage(text, isError = false) {
        const c = document.getElementById('message-container');
        if (!c) return;
        c.textContent = text;
        c.className   = 'message-container ' + (isError ? 'error' : 'success');
        c.classList.add('show');
        setTimeout(() => c.classList.remove('show'), 5000);
    }

    function parseHex(str) {
        const s = (str || '').trim().replace(/^0x/i, '');
        if (!/^[0-9a-fA-F]{1,2}$/.test(s)) return null;
        return parseInt(s, 16);
    }

    function toHex(val) {
        return '0x' + val.toString(16).toUpperCase().padStart(2, '0');
    }

    // ── Konfiguration laden ───────────────────────────────────────────────────

    async function loadConfig() {
        try {
            const resp = await fetch('/api/hardware_setup');
            if (!resp.ok) { showMessage('Fehler beim Laden der Konfiguration.', true); return; }
            const d = await resp.json();

            document.getElementById('cfgSensorIntern').value  = d.sensor_intern  || 'sht31';
            document.getElementById('cfgInternAddress').value = toHex(d.sensor_intern_address ?? 0x44);
            document.getElementById('cfgSensorExtern').value  = d.sensor_extern  || 'aht21';
            document.getElementById('cfgExternAddress').value = toHex(d.sensor_extern_address ?? 0x38);
            document.getElementById('cfgI2cBus').value        = d.i2c_bus        ?? 1;
        } catch (e) {
            showMessage('Verbindungsfehler.', true);
        }
    }

    // ── Konfiguration speichern ───────────────────────────────────────────────

    document.getElementById('btnSaveHardware').addEventListener('click', async () => {
        const internAddr = parseHex(document.getElementById('cfgInternAddress').value);
        const externAddr = parseHex(document.getElementById('cfgExternAddress').value);

        if (internAddr === null) { showMessage('Innensensor-Adresse ungültig (z.B. 0x44).', true); return; }
        if (externAddr === null) { showMessage('Außensensor-Adresse ungültig (z.B. 0x38).', true); return; }

        const payload = {
            sensor_intern:         document.getElementById('cfgSensorIntern').value,
            sensor_intern_address: internAddr,
            sensor_extern:         document.getElementById('cfgSensorExtern').value,
            sensor_extern_address: externAddr,
            i2c_bus:               parseInt(document.getElementById('cfgI2cBus').value) || 1,
        };

        try {
            const resp = await fetch('/api/hardware_setup', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(payload),
            });
            const data = await resp.json();
            if (!resp.ok) {
                const detail = data.details ? data.details.join('; ') : data.error;
                showMessage('Fehler: ' + detail, true);
                return;
            }
            showMessage('Gespeichert. Bitte Service neu starten damit die Änderungen aktiv werden.');
        } catch (e) {
            showMessage('Verbindungsfehler.', true);
        }
    });

    // ── Init ─────────────────────────────────────────────────────────────────

    loadConfig();
});
