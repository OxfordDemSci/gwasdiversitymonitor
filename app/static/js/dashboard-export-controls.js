/* Small controls only; CSV/ZIP code and row materialisation are loaded on demand. */
(function() {
    'use strict';
    function initialise() {
        const registry = window.gwasChartData;
        const dialog = document.getElementById('dashboard-export-dialog');
        const open = document.getElementById('dashboard-export-open');
        const submit = document.getElementById('dashboard-export-submit');
        const chartSelect = document.getElementById('dashboard-export-charts');
        if (!registry || !dialog || !open) return;
        let busy = false;
        const controls = { 'bubble-graph-controls': 'bubbleGraph', 'time-series-controls': 'timeSeries',
            'heat-map-controls': 'heatMap', 'world-map-controls': 'worldMap', 'doughnut-graph-controls': 'doughnutGraph' };
        function update() {
            const ready = registry.canExport();
            const comparisonOption = chartSelect.querySelector('option[value="comparison"]');
            if (registry.ids().includes('comparison') && !comparisonOption) {
                chartSelect.add(new Option('Completed side-by-side comparison', 'comparison'));
            } else if (!registry.ids().includes('comparison') && comparisonOption) comparisonOption.remove();
            open.disabled = !ready;
            submit.disabled = !ready || busy;
            Object.keys(controls).forEach(id => {
                const zone = document.getElementById(id);
                if (!zone) return;
                zone.querySelectorAll('a, button').forEach(control => {
                    control.setAttribute('aria-disabled', String(!ready));
                    if (control.tagName === 'BUTTON') control.disabled = !ready;
                });
            });
        }
        open.addEventListener('click', () => { if (registry.canExport()) dialog.showModal(); });
        document.getElementById('dashboard-export-close').addEventListener('click', () => dialog.close());
        dialog.addEventListener('close', () => open.focus());
        document.getElementById('dashboard-export-form').addEventListener('submit', async event => {
            event.preventDefault();
            if (busy) return;
            busy = true; update();
            try { await registry.download(chartSelect.value); }
            finally { busy = false; update(); }
        });
        document.addEventListener('click', event => {
            const icon = event.target.closest('.icon-zone .icon-download-data');
            const link = event.target.closest('.icon-zone a');
            const zone = (icon || link) && (icon || link).closest('.icon-zone');
            if (!zone || !controls[zone.id]) return;
            event.preventDefault();
            registry.download(controls[zone.id]);
        });
        ['gwas:chartdatachanged', 'gwas:viewchange', 'gwas:datasetstale'].forEach(name => window.addEventListener(name, update));
        update();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialise, {once: true});
    else initialise();
})();
