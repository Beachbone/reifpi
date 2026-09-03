/**
 * archive.js — Historische Charts
 *
 * API: GET /api/archive?hours=N&limit=600
 *
 * Felder im Response:
 *   ts, temp, hum, weight, target_temp, target_hum,
 *   relay_heat, relay_cool, relay_mois, relay_fanu, relay_fana
 */

document.addEventListener('DOMContentLoaded', () => {

    const { DateTime } = luxon;

    const timeframeSelect = document.getElementById('timeframeSelect');
    let tempHumChart   = null;
    let relayChart     = null;
    let refreshTimer   = null;

    // ── Daten laden + Charts rendern ──────────────────────────────────────────

    async function loadAndRender() {
        const hours = parseInt(timeframeSelect.value) || 24;
        const limit = 600;

        try {
            const resp = await fetch(`/api/archive?hours=${hours}&limit=${limit}`);
            if (!resp.ok) return;
            const rows = await resp.json();
            if (!Array.isArray(rows) || rows.length === 0) {
                destroyCharts();
                return;
            }
            renderTempHumChart(rows, hours);
            renderRelayChart(rows, hours);
        } catch (e) {
            console.error('archive.js:', e);
        }
    }

    // ── Temp + Hum Chart ──────────────────────────────────────────────────────

    function renderTempHumChart(rows, hours) {
        const labels      = rows.map(r => DateTime.fromSeconds(r.ts).toMillis());
        const temps       = rows.map(r => r.temp);
        const hums        = rows.map(r => r.hum);
        const targetTemps = rows.map(r => r.target_temp != null ? r.target_temp : null);
        const targetHums  = rows.map(r => r.target_hum  != null ? r.target_hum  : null);

        const datasets = [
            {
                label: 'Temperatur (°C)',
                data:  temps,
                borderColor: '#ff6b6b',
                backgroundColor: 'rgba(255,107,107,0.15)',
                fill: 'origin',
                borderWidth: 2, pointRadius: 0, yAxisID: 'yTemp', tension: 0.3,
            },
            {
                label: 'Soll-Temperatur (°C)',
                data:  targetTemps,
                borderColor: '#ff9800',
                borderDash: [5, 5],
                borderWidth: 1.5, pointRadius: 0, yAxisID: 'yTemp', tension: 0,
            },
            {
                label: 'Luftfeuchtigkeit (%)',
                data:  hums,
                borderColor: '#4fc3f7',
                backgroundColor: 'rgba(79,195,247,0.12)',
                fill: 'origin',
                borderWidth: 2, pointRadius: 0, yAxisID: 'yHum', tension: 0.3,
            },
            {
                label: 'Soll-Feuchtigkeit (%)',
                data:  targetHums,
                borderColor: '#90caf9',
                borderDash: [5, 5],
                borderWidth: 1.5, pointRadius: 0, yAxisID: 'yHum', tension: 0,
            },
        ];

        if (tempHumChart) tempHumChart.destroy();
        const ctx = document.getElementById('tempHumChart').getContext('2d');
        tempHumChart = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: buildOptions(hours, [
                { id: 'yTemp', title: '°C', position: 'left',  min: -10, max: 40 },
                { id: 'yHum',  title: '%',  position: 'right', min:   0, max: 100, gridLines: false },
            ]),
        });
        buildLegend('tempHumLegend', tempHumChart);
    }

    // ── Relay Chart ───────────────────────────────────────────────────────────

    function renderRelayChart(rows, hours) {
        const labels = rows.map(r => DateTime.fromSeconds(r.ts).toMillis());

        const relayDefs = [
            { key: 'relay_heat', label: 'Heizung',    color: '#ff6b6b', offset: 4.5 },
            { key: 'relay_cool', label: 'Kühlung',    color: '#4fc3f7', offset: 3.5 },
            { key: 'relay_mois', label: 'Befeuchter', color: '#a5d6a7', offset: 2.5 },
            { key: 'relay_fanu', label: 'Umluft',     color: '#ce93d8', offset: 1.5 },
            { key: 'relay_fana', label: 'Frischluft', color: '#ffcc80', offset: 0.5 },
        ];

        const datasets = relayDefs.map(def => ({
            label:           def.label,
            data:            rows.map(r => r[def.key] ? def.offset + 0.8 : def.offset),
            borderColor:     def.color,
            backgroundColor: def.color + '99',
            borderWidth:     1,
            pointRadius:     0,
            fill:            { target: { value: def.offset }, above: def.color + '66' },
            stepped:         true,
            yAxisID:         'yRelay',
        }));

        if (relayChart) relayChart.destroy();
        const ctx = document.getElementById('relayChart').getContext('2d');
        relayChart = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: buildOptions(hours, [
                {
                    id: 'yRelay',
                    title: '',
                    position: 'left',
                    min: 0, max: 6,
                    ticks: {
                        stepSize: 1,
                        callback: (v) => {
                            const map = { 4.5: 'Heizung', 3.5: 'Kühlung', 2.5: 'Befeuchter', 1.5: 'Umluft', 0.5: 'Frischluft' };
                            return map[v] || '';
                        },
                    },
                },
            ]),
        });
        buildLegend('relayLegend', relayChart);
    }

    // ── Chart.js Optionen ─────────────────────────────────────────────────────

    function buildOptions(hours, yAxes) {
        const xUnit = hours <= 1 ? 'minute' : hours <= 48 ? 'hour' : 'day';

        const scales = {
            x: {
                type: 'time',
                time: {
                    unit: xUnit,
                    tooltipFormat: 'dd.MM.yyyy HH:mm',
                    displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'dd.MM' },
                },
                grid:  { color: 'rgba(255,255,255,0.05)' },
                ticks: { color: '#aaa' },
            },
        };

        yAxes.forEach(ax => {
            scales[ax.id] = {
                position: ax.position,
                min:      ax.min,
                max:      ax.max,
                grid:     { color: ax.gridLines === false ? 'transparent' : 'rgba(255,255,255,0.05)' },
                ticks:    Object.assign({ color: '#aaa' }, ax.ticks || {}),
                title:    ax.title ? { display: true, text: ax.title, color: '#aaa' } : { display: false },
            };
        });

        return {
            responsive:          true,
            maintainAspectRatio: false,
            animation:           false,
            interaction:         { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => {
                            const ms = items[0]?.parsed?.x;
                            if (!ms) return '';
                            return DateTime.fromMillis(ms).toFormat('dd.MM.yyyy HH:mm');
                        },
                    },
                },
                zoom: {
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
                    pan:  { enabled: true, mode: 'x' },
                },
            },
            scales,
        };
    }

    // ── Legende ───────────────────────────────────────────────────────────────

    function buildLegend(containerId, chart) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';
        chart.data.datasets.forEach((ds, i) => {
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `
                <span class="legend-item-icon" style="border-top: 3px solid ${ds.borderColor}; width:25px;"></span>
                <span>${ds.label}</span>`;
            item.addEventListener('click', () => {
                const meta = chart.getDatasetMeta(i);
                meta.hidden = !meta.hidden;
                item.classList.toggle('hidden', meta.hidden);
                chart.update();
            });
            container.appendChild(item);
        });
    }

    function destroyCharts() {
        if (tempHumChart) { tempHumChart.destroy(); tempHumChart = null; }
        if (relayChart)   { relayChart.destroy();   relayChart   = null; }
    }

    // ── Event-Listener + Init ─────────────────────────────────────────────────

    timeframeSelect.addEventListener('change', () => {
        if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
        loadAndRender();
        refreshTimer = setInterval(loadAndRender, 60000);
    });

    loadAndRender();
    refreshTimer = setInterval(loadAndRender, 60000);
});
