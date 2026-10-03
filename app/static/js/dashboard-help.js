(function(root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.gwasDashboardHelp = api.create(root);
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    function pickExample(payload) {
        return (payload.results || []).find(entry => typeof entry.id === 'string' && typeof entry.text === 'string' && Number(entry.studyCount) > 0);
    }
    function create(browser) {
        const document = browser.document, panels = new Map();
        let adapter, examplesLoading = false, examplesLoaded = false;
        function panelState(id, state, retry) {
            let record = panels.get(id);
            if (!record) {
                const panel = id === 'summary' ? document.querySelector('.box.summary') : document.getElementById(id);
                if (!panel) return;
                const controls = Array.from(panel.querySelectorAll('button, input, select')).map(node => ({node,
                    disabled: node.hasAttribute('data-gwas-bootstrap-disabled') ? false : node.disabled}));
                const status = document.createElement('div'); status.className = 'dashboard-load-status';
                const message = document.createElement('span'); message.setAttribute('role', 'status');
                const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Retry chart';
                status.append(message, button); panel.appendChild(status);
                record = {panel, status, message, button, controls}; panels.set(id, record);
            }
            record.panel.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
            record.status.hidden = state === 'ready';
            record.message.textContent = state === 'error' ? 'This data could not load. Check your connection and retry.' :
                state === 'stale' ? 'Reload this page to use the updated dataset.' : 'Loading data…';
            record.button.hidden = state !== 'error'; record.button.onclick = retry || null;
            record.controls.forEach(control => { control.node.disabled = state === 'ready' ? control.disabled : true; });
        }
        function ready() {
            return browser.gwasDashboardState && browser.gwasDashboardState.isReady() &&
                !browser.gwasDashboardState.isRestoring() && browser.gwasProvenance.isCurrent();
        }
        function syncButtons() {
            document.querySelectorAll('#dashboard-example-buttons button').forEach(button => { button.disabled = !ready(); });
        }
        function exampleButton(label, facet, entry) {
            const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
            button.addEventListener('click', () => { if (ready()) adapter.applyExample(facet, entry); });
            document.getElementById('dashboard-example-buttons').appendChild(button);
        }
        async function loadExamples() {
            if (examplesLoading || examplesLoaded) return;
            examplesLoading = true;
            const status = document.getElementById('dashboard-example-status');
            const retry = document.getElementById('dashboard-example-retry');
            status.textContent = 'Loading a small set of recorded examples…'; retry.hidden = true;
            const datasetId = browser.gwasLoadedProvenance.datasetId || '';
            const urls = {};
            ['funder', 'cohort'].forEach(facet => {
                // Omitting a stage requests the server's bounded 50-entry page.
                urls[facet] = '/api/' + facet + 's?page=1' + (datasetId ? '&datasetId=' + encodeURIComponent(datasetId) : '');
            });
            const loader = browser.GwasDashboardLoading.create(browser, {urls, requests: {}});
            try {
                const results = await Promise.all(['funder', 'cohort'].map(facet => loader.load(facet)));
                document.getElementById('dashboard-example-buttons').replaceChildren();
                exampleButton('All published GWAS', 'all', {id: '', text: ''});
                results.forEach((payload, index) => {
                    const entry = pickExample(payload);
                    if (entry) exampleButton((index ? 'Cohort: ' : 'Funder: ') + entry.text, index ? 'cohort' : 'funder', entry);
                });
                examplesLoaded = true;
                status.textContent = 'Examples replace the funder/cohort selection; other chart settings are kept.';
                syncButtons();
            } catch (error) {
                status.textContent = error.status === 409 ? 'Reload to explore the updated dataset.' : 'Examples could not load. You can still use the dashboard filters.';
                retry.hidden = error.status === 409;
            } finally { examplesLoading = false; }
        }
        function initialise(options) {
            adapter = options;
            ['summary', 'bubbleGraph', 'timeSeries', 'heatMap', 'worldMap', 'doughnutGraph'].forEach(id => panelState(id, 'loading'));
            const details = document.getElementById('dashboard-examples');
            details.addEventListener('toggle', () => { if (details.open) loadExamples(); });
            document.getElementById('dashboard-example-retry').addEventListener('click', loadExamples);
            ['gwas:viewchange', 'gwas:datasetstale'].forEach(event => browser.addEventListener(event, syncButtons));
            if (details.open) loadExamples();
        }
        return {initialise, panelState};
    }
    return {create, pickExample};
});
