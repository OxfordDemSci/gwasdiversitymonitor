/* Provenance belongs to the data served with this page, not a later metadata fetch. */
(function(root, factory) {
    'use strict';
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        root.gwasProvenance = api.create(root.gwasLoadedProvenance || {}, root);
        root.gwasProvenance.initialise();
    }
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    var staleMessage = 'The dataset changed while this view was open. Reload to load one consistent dataset before sharing or exporting.';
    var exportSelector = '#dashboard-copy-link, #funder-data-download, #funder-report-link, '
        + '.icon-zone a, .icon-zone button, #button_svg, #button_png, [data-gwas-export]';

    function clone(value) { return JSON.parse(JSON.stringify(value)); }
    function freeze(value) {
        if (value && typeof value === 'object') {
            Object.keys(value).forEach(function(key) { freeze(value[key]); });
            Object.freeze(value);
        }
        return value;
    }
    function formatTimestamp(value) {
        if (typeof value !== 'string' || !/T.*(?:Z|\+00:00)$/.test(value)) return 'Not recorded';
        var parsed = new Date(value);
        if (!Number.isFinite(parsed.getTime())) return 'Not recorded';
        return new Intl.DateTimeFormat('en-GB', {
            day: 'numeric', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit', timeZone: 'UTC', hourCycle: 'h23'
        }).format(parsed) + ' UTC';
    }

    function create(input, browser) {
        var loaded = freeze(clone(input || {}));
        var stale = false;
        var initialised = false;
        var document = browser && browser.document;
        var coverageLoaded = false;
        var coverageRequest = null;

        function isCurrent(datasetId) {
            return !stale && (!arguments.length || !loaded.datasetId || datasetId === loaded.datasetId);
        }
        function showStale() {
            if (!document) return;
            var alert = document.getElementById('dashboard-dataset-stale');
            if (alert) alert.hidden = false;
            document.querySelectorAll(exportSelector).forEach(function(control) {
                control.setAttribute('aria-disabled', 'true');
                if (control.tagName === 'BUTTON') control.disabled = true;
            });
        }
        function markStale() {
            var wasStale = stale;
            stale = true;
            showStale();
            if (!wasStale && browser && browser.dispatchEvent && browser.CustomEvent) {
                browser.dispatchEvent(new browser.CustomEvent('gwas:datasetstale', {
                    detail: {datasetId: loaded.datasetId || null}
                }));
            }
            return false;
        }
        function reload() {
            var dashboard = browser.gwasDashboardState;
            var href = dashboard && dashboard.isReady() && !dashboard.isRestoring()
                ? dashboard.url() : browser.location.href;
            var destination = new URL(href);
            // Shared views describe settings; the newly loaded page chooses its dataset.
            destination.searchParams.delete('datasetId');
            browser.location.assign(destination.href);
        }
        function loadCoverage() {
            if (coverageLoaded || coverageRequest || !document) return coverageRequest;
            var status = document.getElementById('provenance-coverage-status');
            var retry = document.getElementById('provenance-coverage-retry');
            if (!status || !retry) return null;
            if (stale) { status.textContent = staleMessage; return null; }
            retry.hidden = true;
            status.textContent = 'Loading metadata coverage…';
            var query = new URLSearchParams({coverage: '1'});
            if (loaded.datasetId) query.set('datasetId', loaded.datasetId);
            coverageRequest = browser.fetch('/api/provenance?' + query.toString(), {credentials: 'same-origin'})
                .then(function(response) {
                    if (response.status === 409) {
                        markStale();
                        throw new Error(staleMessage);
                    }
                    if (!response.ok) throw new Error('Coverage could not be loaded. Please retry.');
                    return response.json();
                }).then(function(data) {
                    if (!isCurrent(data.datasetId)) {
                        markStale();
                        throw new Error(staleMessage);
                    }
                    var coverage = data.coverage || {};
                    var total = coverage.study_count;
                    var countFormat = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 0});
                    var shareFormat = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 1});
                    if (!Number.isInteger(total) || total < 0) throw new Error('Coverage could not be loaded. Please retry.');
                    ['funder', 'cohort'].forEach(function(facet) {
                        var count = coverage[facet + '_linked_study_count'];
                        if (!Number.isInteger(count) || count < 0 || count > total) throw new Error('Coverage could not be loaded. Please retry.');
                        document.getElementById('provenance-' + facet + '-coverage').textContent = total
                            ? shareFormat.format(count * 100 / total) + '% (' + countFormat.format(count) + ' of ' + countFormat.format(total) + ')'
                            : 'No studies recorded';
                    });
                    document.getElementById('provenance-coverage-values').hidden = false;
                    status.textContent = '';
                    coverageLoaded = true;
                }).catch(function(error) {
                    status.textContent = stale ? staleMessage : 'Coverage could not be loaded. Check your connection and retry.';
                    retry.hidden = stale;
                }).finally(function() { coverageRequest = null; });
            return coverageRequest;
        }
        function initialise() {
            if (!document || initialised) return;
            initialised = true;
            function render() {
                ['generatedAt', 'lastSuccessfulFetchAt', 'lastSuccessfulRunAt'].forEach(function(key) {
                    var node = document.querySelector('[data-provenance="' + key + '"]');
                    if (node) node.textContent = formatTimestamp(loaded[key]);
                });
                var checkSummary = document.querySelector('[data-provenance="checkSummary"]');
                if (checkSummary) {
                    checkSummary.textContent = formatTimestamp(loaded.lastSuccessfulFetchAt) === 'Not recorded'
                        ? 'check not recorded'
                        : 'checked ' + new Intl.DateTimeFormat('en-GB', {
                            day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'
                        }).format(new Date(loaded.lastSuccessfulFetchAt));
                }
                var reloadButton = document.getElementById('dashboard-dataset-reload');
                if (reloadButton) reloadButton.addEventListener('click', reload);
                var details = document.getElementById('dashboard-provenance');
                if (details) details.addEventListener('toggle', function() { if (details.open) loadCoverage(); });
                var retry = document.getElementById('provenance-coverage-retry');
                if (retry) retry.addEventListener('click', loadCoverage);
                var compare = document.getElementById('provenance-compare');
                if (compare) compare.addEventListener('click', function() {
                    var opener = document.getElementById('dashboard-compare-open');
                    if (opener) opener.click();
                });
                if (stale) showStale();
            }
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render, {once: true});
            else render();
            // Capture runs before existing chart download/copy handlers, including
            // dynamically added controls. Future exports can also use isCurrent().
            document.addEventListener('click', function(event) {
                if (stale && event.target.closest && event.target.closest(exportSelector)) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    showStale();
                }
            }, true);
        }
        return Object.freeze({
            loaded: loaded,
            capture: function() { return clone(loaded); },
            isCurrent: isCurrent,
            markStale: markStale,
            message: staleMessage,
            initialise: initialise,
            reload: reload
        });
    }
    return {create: create, formatTimestamp: formatTimestamp};
});
