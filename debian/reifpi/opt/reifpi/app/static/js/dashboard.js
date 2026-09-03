/**
 * dashboard.js — Reifeschrank Hauptsteuerung
 *
 * Kommuniziert ausschließlich mit der neuen REST-API:
 *   GET  /api/status        → Istwerte + Konfiguration
 *   POST /api/targets       → Sollwerte + Hysteresen setzen
 *   POST /api/mode          → Betriebsmodus setzen
 *   POST /api/power         → Ein/Aus
 *   POST /api/relay/<n>/<on|off>   → Manuelles Schalten (nur Modus 3)
 *   GET  /api/programs/running     → Laufendes Programm
 *   POST /api/programs/stop        → Programm stoppen
 *   POST /api/safe_mode/reset      → Safe-Mode zurücksetzen
 */

document.addEventListener('DOMContentLoaded', () => {

    // ── DOM-Referenzen ────────────────────────────────────────────────────────
    const msgContainer          = document.getElementById('message-container');

    const setTempDisplay        = document.getElementById('setTempDisplay');
    const currentTempDisplay    = document.getElementById('currentTempDisplay');
    const setHumidityDisplay    = document.getElementById('setHumidityDisplay');
    const currentHumidityDisplay= document.getElementById('currentHumidityDisplay');

    const externTempDisplay      = document.getElementById('externTempDisplay');
    const externHumDisplay       = document.getElementById('externHumDisplay');
    const absHumInternDisplay    = document.getElementById('absHumInternDisplay');
    const absHumExternDisplay    = document.getElementById('absHumExternDisplay');

    const setTempInput          = document.getElementById('setTemp');
    const setHumInput           = document.getElementById('setHumidity');
    const hystCoolInput         = document.getElementById('temp_hyst_cool');
    const hystHeatInput         = document.getElementById('temp_hyst_heat');
    const hystMoisInput         = document.getElementById('hum_hyst_mois');
    const hystFanaInput         = document.getElementById('hum_hyst_fana');

    const modeSlider            = document.getElementById('modeSlider');
    const modeStatusText        = document.getElementById('modeStatusText');
    const modeDigits            = [0, 1, 2, 3].map(i => document.getElementById('modeDigit' + i));

    const checkUnlock           = document.getElementById('checkUnlock');
    const ledUnlock             = document.getElementById('ledUnlock');
    const picUnlock             = document.getElementById('picUnlock');

    const checkPower            = document.getElementById('checkPower');
    const ledPower              = document.getElementById('ledPower');
    const picPower              = document.getElementById('picPower');

    const programSection        = document.getElementById('program-status-section');
    const runningProgramName    = document.getElementById('runningProgramName');
    const runningProgramStep    = document.getElementById('runningProgramStep');
    const runningProgramEnd     = document.getElementById('runningProgramEnd');
    const btnStopProgram        = document.getElementById('btnStopProgram');

    const safeModeBanner        = document.getElementById('safe-mode-banner');
    const safeModeReason        = document.getElementById('safeModeReason');
    const btnResetSafeMode      = document.getElementById('btnResetSafeMode');

    // Relais: { relay-name: { led, pic, checkbox } }
    const relays = {
        heat: { led: document.getElementById('ledHeating'),  pic: document.getElementById('picHeating'),  cb: document.getElementById('checkHeating') },
        cool: { led: document.getElementById('ledCooling'),  pic: document.getElementById('picCooling'),  cb: document.getElementById('checkCooling') },
        fanu: { led: document.getElementById('ledFanU'),     pic: document.getElementById('picFanU'),     cb: document.getElementById('checkFanU')    },
        fana: { led: document.getElementById('ledFanA'),     pic: document.getElementById('picFanA'),     cb: document.getElementById('checkFanA')    },
        mois: { led: document.getElementById('ledMois'),     pic: document.getElementById('picMois'),     cb: document.getElementById('checkMois')    },
    };

    // Navigations-Items
    const navItems = [
        { container: document.getElementById('navPrograms'), cb: document.getElementById('checkPrograms'), led: document.getElementById('ledPrograms'), pic: document.getElementById('picPrograms'), url: '/programs' },
        { container: document.getElementById('navArchive'),  cb: document.getElementById('checkArchive'),  led: document.getElementById('ledArchive'),  pic: document.getElementById('picArchive'),  url: '/archive'  },
        { container: document.getElementById('navSettings'), cb: document.getElementById('checkSettings'), led: document.getElementById('ledSettings'), pic: document.getElementById('picSettings'), url: '/settings' },
    ];

    // Alle editierbaren Steuerelemente (werden bei power=off deaktiviert)
    const controllableInputs = [
        setTempInput, setHumInput, hystCoolInput, hystHeatInput, hystMoisInput, hystFanaInput,
        ...['btnTempMinus','btnTempPlus','btnHumidityMinus','btnHumidityPlus',
            'btnTempHystCoolMinus','btnTempHystCoolPlus','btnTempHystHeatMinus','btnTempHystHeatPlus',
            'btnHumHystMoisMinus','btnHumHystMoisPlus','btnHumHystFanAMinus','btnHumHystFanAPlus',
        ].map(id => document.getElementById(id)),
    ];

    // ── Zustand ───────────────────────────────────────────────────────────────
    let manualMode       = false;
    let fetchInterval    = null;
    let unlockTimeout    = null;
    let msgTimeout       = null;
    const AUTO_LOCK_MS   = 10000;

    // Input-Felder die beim Fokus nicht überschrieben werden
    const protectedInputs = new Set([setTempInput, setHumInput, hystCoolInput, hystHeatInput, hystMoisInput, hystFanaInput]);

    // ── Hilfsfunktionen ───────────────────────────────────────────────────────

    function showMsg(text, type = 'info', ms = 3000) {
        if (!msgContainer) return;
        msgContainer.className = 'message-container ' + type;
        msgContainer.textContent = text;
        msgContainer.classList.add('show');
        if (msgTimeout) clearTimeout(msgTimeout);
        msgTimeout = setTimeout(() => msgContainer.classList.remove('show'), ms);
    }

    function setLed(led, pic, active) {
        if (led)  led.classList.toggle('active', active);
        if (pic) {
            if (active) { pic.classList.remove('pictogram-inactive'); pic.classList.add('pictogram-active'); }
            else        { pic.classList.add('pictogram-inactive');    pic.classList.remove('pictogram-active'); }
        }
    }

    function fmt1(v) { return v != null ? parseFloat(v).toFixed(1) : '---'; }
    function fmt2(v) { return v != null ? parseFloat(v).toFixed(2) : '-.--'; }

    function updateDisplays(st) {
        if (!st) return;
        currentTempDisplay.textContent     = fmt1(st.temp_intern) + '°C';
        currentHumidityDisplay.textContent = fmt1(st.hum_intern)  + '%';
        setTempDisplay.textContent         = fmt1(st.target_temp) + '°C';
        setHumidityDisplay.textContent     = fmt1(st.target_hum)  + '%';

        externTempDisplay.textContent      = fmt1(st.temp_extern)    + '°C';
        externHumDisplay.textContent       = fmt1(st.hum_extern)     + '%';
        absHumInternDisplay.textContent    = fmt2(st.abs_hum_intern);
        absHumExternDisplay.textContent    = fmt2(st.abs_hum_extern);
    }

    function setControlsEnabled(enabled) {
        controllableInputs.forEach(el => { if (el) el.disabled = !enabled; });
    }

    function clamp(val, min, max, step) {
        let v = parseFloat(val);
        if (isNaN(v)) v = min;
        v = Math.max(min, Math.min(max, v));
        const dec = step.toString().includes('.') ? step.toString().split('.')[1].length : 0;
        return v.toFixed(dec);
    }

    function adjustInput(inp, dir) {
        const step = parseFloat(inp.step) || 1;
        const min  = parseFloat(inp.min);
        const max  = parseFloat(inp.max);
        inp.value  = clamp(parseFloat(inp.value) + dir * step, min, max, step);
        setTempDisplay.textContent      = fmt1(setTempInput.value) + '°C';
        setHumidityDisplay.textContent  = fmt1(setHumInput.value)  + '%';
        sendTargets();
    }

    // ── API-Aufrufe ───────────────────────────────────────────────────────────

    async function apiPost(url, body) {
        const resp = await fetch(url, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body),
        });
        return resp.json();
    }

    async function sendTargets() {
        if (manualMode) { showMsg('Sollwerte nur im Automatik-Modus änderbar.', 'warning'); return; }
        const payload = {
            target_temp:   parseFloat(setTempInput.value),
            target_hum:    parseFloat(setHumInput.value),
            temp_hyst_cool: parseFloat(hystCoolInput.value),
            temp_hyst_heat: parseFloat(hystHeatInput.value),
            hum_hyst_mois:  parseFloat(hystMoisInput.value),
            hum_hyst_fana:  parseFloat(hystFanaInput.value),
        };
        try {
            const r = await apiPost('/api/targets', payload);
            if (r.ok) { showMsg('Sollwerte gespeichert.', 'success'); fetchStatus(); }
            else       showMsg('Fehler: ' + (r.error || 'unbekannt'), 'error', 5000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    }

    async function sendMode(mode) {
        try {
            const r = await apiPost('/api/mode', { mode });
            if (!r.ok) showMsg('Modus-Fehler: ' + (r.error || ''), 'error', 5000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error', 5000);
            fetchStatus();
        }
    }


    async function sendRelay(name, on) {
        try {
            const action = on ? 'on' : 'off';
            const r = await fetch(`/api/relay/${name}/${action}`, { method: 'POST' });
            const j = await r.json();
            if (!j.ok) showMsg('Relais-Fehler: ' + (j.error || ''), 'error', 5000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    }

    // ── Status abrufen + UI aktualisieren ─────────────────────────────────────

    async function fetchStatus() {
        try {
            const resp = await fetch('/api/status');
            if (!resp.ok) { showMsg('Status nicht verfügbar.', 'error'); return; }
            const st = await resp.json();

            const active = document.activeElement;
            const isTyping = protectedInputs.has(active);

            // Istwerte immer aktualisieren
            updateDisplays(st);

            // Sollwerte nur updaten wenn nicht editiert
            if (!manualMode && !isTyping) {
                setTempInput.value  = parseFloat(st.target_temp).toFixed(1);
                setHumInput.value   = parseFloat(st.target_hum).toFixed(1);
                hystCoolInput.value = parseFloat(st.temp_hyst_cool).toFixed(1);
                hystHeatInput.value = parseFloat(st.temp_hyst_heat).toFixed(1);
                hystMoisInput.value = parseFloat(st.hum_hyst_mois).toFixed(1);
                hystFanaInput.value = parseFloat(st.hum_hyst_fana).toFixed(1);
            }

            // Modus
            const mode = parseInt(st.mode ?? 0);
            if (!modeSlider.disabled || document.activeElement !== modeSlider) {
                modeSlider.value = mode;
            }
            modeDigits.forEach((d, i) => d.classList.toggle('active', i === mode));

            if (mode === 3) {
                manualMode = true;
                document.body.classList.add('manual-mode-active');
            } else {
                manualMode = false;
                document.body.classList.remove('manual-mode-active');
            }

            // Relais
            Object.entries(relays).forEach(([name, r]) => {
                const active = !!st['relay_' + name];
                setLed(r.led, r.pic, active);
                if (!manualMode) r.cb.checked = active;
            });

            // Power-LED — Pi läuft, also immer AN
            setLed(ledPower, picPower, true);
            setControlsEnabled(!manualMode);

            // Safe-Mode
            if (st.safe_mode) {
                safeModeBanner.style.display = '';
                safeModeReason.textContent   = st.safe_mode_reason || '';
            } else {
                safeModeBanner.style.display = 'none';
            }

            // Laufendes Programm
            if (st.active_program_id) {
                runningProgramName.textContent = st.active_program_name || '';
                runningProgramStep.textContent = `Schritt ${st.active_program_step}`;
                if (st.program_step_end) {
                    const end = new Date(st.program_step_end * 1000);
                    runningProgramEnd.textContent = end.toLocaleString('de-DE', {
                        day: '2-digit', month: '2-digit', year: 'numeric',
                        hour: '2-digit', minute: '2-digit',
                    }) + ' Uhr';
                }
                programSection.style.display = '';
            } else {
                programSection.style.display = 'none';
            }

        } catch (e) {
            console.error('fetchStatus:', e);
        }
    }

    function startPolling(interval) {
        if (fetchInterval) clearInterval(fetchInterval);
        fetchInterval = setInterval(fetchStatus, Math.max(5000, interval * 1000));
    }

    // ── Event-Listener ────────────────────────────────────────────────────────

    // +/- Buttons
    [
        ['btnTempMinus',         setTempInput,  -1],
        ['btnTempPlus',          setTempInput,   1],
        ['btnHumidityMinus',     setHumInput,   -1],
        ['btnHumidityPlus',      setHumInput,    1],
        ['btnTempHystCoolMinus', hystCoolInput, -1],
        ['btnTempHystCoolPlus',  hystCoolInput,  1],
        ['btnTempHystHeatMinus', hystHeatInput, -1],
        ['btnTempHystHeatPlus',  hystHeatInput,  1],
        ['btnHumHystMoisMinus',  hystMoisInput, -1],
        ['btnHumHystMoisPlus',   hystMoisInput,  1],
        ['btnHumHystFanAMinus',  hystFanaInput, -1],
        ['btnHumHystFanAPlus',   hystFanaInput,  1],
    ].forEach(([id, inp, dir]) => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.addEventListener('click', () => adjustInput(inp, dir));
        btn.addEventListener('touchstart', e => { e.preventDefault(); adjustInput(inp, dir); });
    });

    // Input change → send
    [setTempInput, setHumInput, hystCoolInput, hystHeatInput, hystMoisInput, hystFanaInput].forEach(inp => {
        inp.addEventListener('change', sendTargets);
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') sendTargets(); });
    });

    // Modus-Freischalten (Lock-Icon)
    checkUnlock.addEventListener('change', () => {
        const unlock = checkUnlock.checked;
        modeSlider.disabled = !unlock;
        setLed(ledUnlock, picUnlock, unlock);
        modeStatusText.textContent = unlock ? '(frei)' : '(gesperrt)';
        if (unlock) {
            showMsg('Modus-Einstellung freigeschaltet.', 'info');
            if (unlockTimeout) clearTimeout(unlockTimeout);
            unlockTimeout = setTimeout(lockMode, AUTO_LOCK_MS);
        } else {
            showMsg('Modus-Einstellung gesperrt.', 'info');
            if (unlockTimeout) { clearTimeout(unlockTimeout); unlockTimeout = null; }
        }
    });

    function lockMode() {
        checkUnlock.checked = false;
        modeSlider.disabled = true;
        setLed(ledUnlock, picUnlock, false);
        modeStatusText.textContent = '(gesperrt)';
        unlockTimeout = null;
        showMsg('Modus-Einstellung automatisch gesperrt.', 'info');
    }

    // Modus-Slider
    modeSlider.addEventListener('input', async () => {
        if (unlockTimeout) { clearTimeout(unlockTimeout); unlockTimeout = setTimeout(lockMode, AUTO_LOCK_MS); }
        const mode = parseInt(modeSlider.value);
        modeDigits.forEach((d, i) => d.classList.toggle('active', i === mode));
        showMsg('Sende Modus-Änderung…', 'info', 1000);
        await sendMode(mode);
        if (mode === 3) {
            manualMode = true;
            document.body.classList.add('manual-mode-active');
            showMsg('Manueller Modus aktiv — Ausgänge direkt schaltbar.', 'warning', 5000);
        } else {
            manualMode = false;
            document.body.classList.remove('manual-mode-active');
            showMsg(`Modus ${mode} gesetzt.`, 'success');
        }
    });

    // Relais-Checkboxen (manuell)
    Object.entries(relays).forEach(([name, r]) => {
        r.cb.addEventListener('change', () => {
            if (!manualMode) {
                r.cb.checked = !r.cb.checked;
                showMsg('Ausgänge nur im manuellen Modus (3) schaltbar.', 'warning', 4000);
                return;
            }
            setLed(r.led, r.pic, r.cb.checked);
            sendRelay(name, r.cb.checked);
        });
    });

    // Power = Shutdown
    checkPower.addEventListener('change', async () => {
        checkPower.checked = false; // Checkbox zurücksetzen
        const ok = confirm(
            'Raspberry Pi wirklich herunterfahren?\n\n' +
            'Alle Relais werden ausgeschaltet.\n' +
            'Der Pi muss anschließend physisch neu gestartet werden.'
        );
        if (!ok) return;
        setLed(ledPower, picPower, false);
        showMsg('Pi wird heruntergefahren…', 'warning', 10000);
        try {
            await fetch('/api/system/shutdown', { method: 'POST' });
        } catch (_) { /* Verbindung bricht ab — erwartet */ }
    });

    // Programm stoppen
    btnStopProgram.addEventListener('click', async () => {
        if (!confirm('Laufendes Programm wirklich stoppen?')) return;
        try {
            const r = await fetch('/api/programs/stop', { method: 'POST' });
            const j = await r.json();
            if (j.ok) { showMsg('Programm gestoppt.', 'success'); fetchStatus(); }
            else       showMsg('Fehler: ' + (j.error || ''), 'error', 5000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    });

    // Safe-Mode Reset
    btnResetSafeMode.addEventListener('click', async () => {
        try {
            const r = await fetch('/api/safe_mode/reset', { method: 'POST' });
            const j = await r.json();
            if (j.ok) { showMsg('Safe-Mode zurückgesetzt.', 'success'); fetchStatus(); }
            else       showMsg('Fehler: ' + (j.error || ''), 'error', 5000);
        } catch (e) {
            showMsg('Verbindungsfehler.', 'error', 5000);
        }
    });

    // Navigation (Checkboxen als Klick-Fläche)
    navItems.forEach(item => {
        const target = item.container || item.cb;
        if (!target) return;
        target.addEventListener('click', e => {
            e.preventDefault();
            setLed(item.led, item.pic, true);
            setTimeout(() => window.location.href = item.url, 150);
        });
        if (target.style !== undefined) target.style.cursor = 'pointer';
    });

    // ── Initialisierung ───────────────────────────────────────────────────────
    fetchStatus().then(() => startPolling(30));

});
