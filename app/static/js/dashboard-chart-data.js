/* Lazy, shared chart snapshots for exports and accessible tables. */
(function(root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.gwasChartData = api.create(root);
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    const chartIds = ['bubbleGraph', 'timeSeries', 'heatMap', 'worldMap', 'doughnutGraph'];
    function copy(value) { return JSON.parse(JSON.stringify(value)); }
    function freeze(value) {
        if (value && typeof value === 'object') {
            Object.keys(value).forEach(key => freeze(value[key]));
            Object.freeze(value);
        }
        return value;
    }
    function create(browser) {
        const providers = new Map(), preparations = new Map();
        let revision = 0, selectionReady = false, committedFacets = '', exporterPromise;
        function state() { return browser.gwasDashboardState.capture(); }
        function facets() {
            const view = state();
            return JSON.stringify([view.funders, view.cohorts]);
        }
        function changed(id) {
            revision += 1;
            if (browser.dispatchEvent && browser.CustomEvent) browser.dispatchEvent(
                new browser.CustomEvent('gwas:chartdatachanged', {detail: {id: id || null, revision: revision}})
            );
        }
        function setSelectionReady(ready, commitFacets) {
            selectionReady = ready;
            // Only a successful entity-data load may commit a new selection.
            // A metric/stage redraw can still be drawing retained old data
            // after a failed entity request, so it must keep that identity.
            if (ready && commitFacets !== false && browser.gwasDashboardState) committedFacets = facets();
            changed();
        }
        function canExport() {
            const dashboard = browser.gwasDashboardState, provenance = browser.gwasProvenance;
            const toolbar = browser.document && browser.document.querySelector('.funder-toolbar');
            return !!(selectionReady && dashboard && dashboard.isReady() && !dashboard.isRestoring()
                && provenance && provenance.loaded.datasetId && provenance.isCurrent()
                && (!toolbar || toolbar.getAttribute('aria-busy') !== 'true')
                && committedFacets === facets() && chartIds.every(id => providers.has(id)));
        }
        function signature() {
            return JSON.stringify({view: state(), datasetId: browser.gwasProvenance.loaded.datasetId});
        }
        function assertCurrent(snapshot) {
            if (!canExport()) throw new Error('Wait for the selected view to finish loading successfully before exporting.');
            if (snapshot && (snapshot.revision !== revision || snapshot.signature !== signature())) {
                throw new Error('The view changed while preparing the export. Please export the current view again.');
            }
        }
        function snapshot(id) {
            assertCurrent();
            const provider = providers.get(id);
            if (!provider) throw new Error('This chart has not finished loading.');
            const source = provider();
            if (!source || !Array.isArray(source.columns) || !Number.isInteger(source.rowCount)
                    || source.rowCount < 0 || typeof source.rowAt !== 'function') {
                throw new Error('This chart cannot provide export data yet.');
            }
            const result = {
                id: id, title: source.title, columns: freeze(copy(source.columns)), rowCount: source.rowCount,
                rowAt: function(index) {
                    if (!Number.isInteger(index) || index < 0 || index >= source.rowCount) throw new RangeError('Row is out of range.');
                    return source.rowAt(index);
                },
                settings: freeze(copy(source.settings || {})), methodology: freeze(copy(source.methodology || [])),
                view: freeze(copy(state())), provenance: freeze(browser.gwasProvenance.capture()),
                shareUrl: browser.gwasDashboardState.url(), exportedAt: new Date().toISOString(),
                revision: revision, signature: signature()
            };
            return Object.freeze(result);
        }
        function reportError(message) {
            if (!browser.document) return;
            ['dashboard-export-status', 'dashboard-share-status'].forEach(id => {
                const node = browser.document.getElementById(id);
                if (node) node.textContent = message;
            });
        }
        function loadExporter() {
            if (browser.gwasExports) return Promise.resolve(browser.gwasExports);
            if (!exporterPromise) {
                exporterPromise = new Promise((resolve, reject) => {
                    const script = browser.document.createElement('script');
                    script.src = browser.gwasStaticAssets.exportBundle;
                    script.onload = function() {
                        if (browser.gwasExports) resolve(browser.gwasExports);
                        else reject(new Error('The export tools could not initialise. Please retry.'));
                    };
                    script.onerror = function() { script.remove(); reject(new Error('The export tools could not load. Check your connection and retry.')); };
                    browser.document.head.appendChild(script);
                }).catch(error => { exporterPromise = null; throw error; });
            }
            return exporterPromise;
        }
        async function download(id) {
            try {
                const captured = (id === 'all' ? chartIds : [id]).map(snapshot);
                const exporter = await loadExporter();
                captured.forEach(assertCurrent);
                return await exporter.downloadSnapshots(captured);
            } catch (error) { reportError(error.message); return false; }
        }
        if (browser.addEventListener) browser.addEventListener('gwas:datasetstale', () => setSelectionReady(false));
        return {
            chartIds: chartIds.slice(),
            register: function(id, provider) { providers.set(id, provider); changed(id); },
            invalidate: function(id) { providers.delete(id); preparations.delete(id); changed(id); },
            changed: changed, setSelectionReady: setSelectionReady, canExport: canExport,
            snapshot: snapshot, assertCurrent: assertCurrent, ids: () => Array.from(providers.keys()),
            setImagePreparation: (id, prepare) => preparations.set(id, prepare),
            prepareImage: function(id) { if (preparations.has(id)) preparations.get(id)(); },
            loadExporter: loadExporter, download: download, reportError: reportError
        };
    }
    return {create: create};
});
