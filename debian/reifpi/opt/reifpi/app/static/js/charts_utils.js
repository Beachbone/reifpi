// charts_utils.js

/**
 * Berechnet den Start-Zeitstempel basierend auf dem ausgewählten Zeitrahmen
 * und einem Referenz-Zeitstempel (z.B. dem neuesten Eintrag in der DB).
 * @param {string} timeframe - Der Zeitrahmen ('last60min', 'last6h', 'last12h', 'last24h', 'last7d', 'last30d', 'allTime').
 * @param {number} referenceTimestamp - Der UNIX-Zeitstempel (Sekunden) des neuesten Eintrags in der DB.
 * @returns {number} UNIX-Timestamp (Sekunden) für den Startzeitpunkt.
 */
function calculateStartTime(timeframe, referenceTimestamp) {
    const { DateTime } = luxon;
    // WICHTIG: Luxon benötigt Millisekunden, referenceTimestamp ist in Sekunden.
    const referenceDateTime = DateTime.fromSeconds(referenceTimestamp); 
    let startTime = 0; // Standard: Alle Daten

    switch (timeframe) {
        case 'last60min':
            startTime = referenceDateTime.minus({ minutes: 60 }).toSeconds();
            break;
        case 'last6h':
            startTime = referenceDateTime.minus({ hours: 6 }).toSeconds();
            break;
        case 'last12h':
            startTime = referenceDateTime.minus({ hours: 12 }).toSeconds();
            break;
        case 'last24h':
            startTime = referenceDateTime.minus({ hours: 24 }).toSeconds();
            break;
        case 'last7d':
            startTime = referenceDateTime.minus({ days: 7 }).toSeconds();
            break;
        case 'last30d':
            startTime = referenceDateTime.minus({ days: 30 }).toSeconds();
            break;
        case 'allTime':
        default:
            startTime = 0; // Holt alle Daten ab dem frühestmöglichen Zeitpunkt (PHP kümmert sich um 0)
            break;
    }
    return Math.floor(startTime);
}

/**
 * Bereitet die Daten für die Chart.js Datasets vor.
 * @param {Array<Object>} rawData - Die rohen Daten vom Server.
 * @returns {Object} Ein Objekt mit aufbereiteten Daten für Charts.
 */
function prepareChartData(rawData) {
    const { DateTime } = luxon;

    const timestamps = rawData.map(row => DateTime.fromSeconds(parseInt(row.timestamp)).toMillis());
    const temperatures = rawData.map(row => parseFloat(row.temp));
    const humidities = rawData.map(row => parseFloat(row.hum));

    const targetTemperatures = rawData.map(row => row.target_temp !== undefined && row.target_temp !== null ? parseFloat(row.target_temp) : null);
    const targetHumidities = rawData.map(row => row.target_hum !== undefined && row.target_hum !== null ? parseFloat(row.target_hum) : null);

    const heatingData = rawData.map(row => parseInt(row.heat));
    const coolingData = rawData.map(row => parseInt(row.cool));
    const moistData = rawData.map(row => parseInt(row.moist));
    const fanAData = rawData.map(row => parseInt(row.fan_a));
    const fanUData = rawData.map(row => parseInt(row.fan_u));

    return {
        timestamps,
        temperatures,
        humidities,
        targetTemperatures,
        targetHumidities,
        heatingData,
        coolingData,
        moistData,
        fanAData,
        fanUData
    };
}

/**
 * Grundlegende Optionen für Zeitachsen in Chart.js.
 * Passt die Einheit basierend auf dem Zeitrahmen an.
 * @param {string} timeframe - Der aktuelle Zeitrahmen.
 * @returns {Object} Chart.js Skalenoptionen für die X-Achse.
 */
function getTimeAxisOptions(timeframe) {
    let unit;
    let displayFormats;

    switch (timeframe) {
        case 'last60min':
        case 'last6h':
        case 'last12h':
        case 'last24h':
            unit = 'hour';
            displayFormats = {
                hour: 'HH:mm',
                day: 'dd.MM HH:mm' // Für den Fall, dass es über einen Tag geht
            };
            break;
        case 'last7d':
            unit = 'day';
            displayFormats = {
                day: 'dd.MM',
                week: 'dd.MM'
            };
            break;
        case 'last30d':
            unit = 'day'; // Oder 'week', je nach Datenmenge
            displayFormats = {
                day: 'dd.MM',
                week: 'dd.MM'
            };
            break;
        case 'allTime':
        default:
            unit = 'day'; // Standard für längere Zeiträume
            displayFormats = {
                day: 'dd.MM.yyyy',
                week: 'dd.MM.yyyy',
                month: 'MMM yyyy',
                year: 'yyyy'
            };
            break;
    }

    return {
        type: 'time',
        time: {
            unit: unit,
            tooltipFormat: 'dd.MM.yyyy HH:mm',
            displayFormats: displayFormats
        },
        title: {
            display: true,
            text: 'Zeitpunkt'
        }
    };
}

/**
 * Allgemeine Chart.js Plugin-Optionen (Tooltips, Zoom).
 * @returns {Object} Chart.js Plugin-Optionen.
 */
function getPluginOptions() {
    return {
        tooltip: {
            mode: 'index',
            intersect: false
        },
        zoom: { // Optional: Für Zoom- und Pan-Funktionalität
            zoom: {
                wheel: { enabled: true },
                pinch: { enabled: true },
                mode: 'x',
            },
            pan: {
                enabled: true,
                mode: 'x',
            }
        }
    };
}
