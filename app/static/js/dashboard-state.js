/* Versioned, bounded dashboard links. No chart data or credentials enter URLs. */
(function(root, factory) {
    'use strict';
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.GwasDashboardState = api;
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    var keys = [
        'view', 'metric', 'stage', 'funders', 'cohorts', 'heatYear', 'mapYear',
        'doughnutYear', 'doughnutTerm', 'associations', 'ancestry', 'notRecorded',
        'bubbleTerm', 'bubbleAncestries', 'traits', 'mapZoom'
    ];
    // Stay below common 8 KiB proxy request-line limits after encoding.
    var maxURLLength = 7500;

    function text(value, limit) {
        return typeof value === 'string' && value.length <= (limit || 250) &&
            !/[\u0000-\u001f\u007f]/.test(value) ? value : '';
    }
    function slug(value) {
        return typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$/.test(value)
            ? value : '';
    }
    function list(value) {
        return Array.isArray(value) ? value.slice(0, 50) : [];
    }
    function ids(value) {
        return Array.from(new Set(list(value).map(slug).filter(Boolean))).sort();
    }
    function year(value) {
        return /^(19|20)\d{2}$/.test(String(value || '')) ? String(value) : null;
    }
    function normalize(input) {
        input = input && typeof input === 'object' ? input : {};
        var heat = input.heatMap || {}, map = input.worldMap || {};
        var doughnut = input.doughnut || {}, series = input.timeSeries || {};
        var bubble = input.bubble || {}, zoom = map.zoom || {};
        var traitIds = new Set();
        return {
            version: 1,
            metric: input.metric === 'studies' ? 'studies' : 'participants',
            stage: input.stage === 'replication' ? 'replication' : 'discovery',
            funders: ids(input.funders), cohorts: ids(input.cohorts),
            heatMap: {year: year(heat.year)},
            worldMap: {
                year: year(map.year),
                zoom: {
                    x: Number.isFinite(zoom.x) && Math.abs(zoom.x) <= 100000 ? zoom.x : 0,
                    y: Number.isFinite(zoom.y) && Math.abs(zoom.y) <= 100000 ? zoom.y : 0,
                    k: Number.isFinite(zoom.k) && zoom.k >= 1 && zoom.k <= 20 ? zoom.k : 1
                }
            },
            doughnut: {
                year: year(doughnut.year), parentTerm: slug(doughnut.parentTerm) || 'all',
                associations: doughnut.associations === true
            },
            timeSeries: {
                ancestry: slug(series.ancestry) || 'all-ancestries',
                notRecorded: series.notRecorded === true
            },
            bubble: {
                parentTerm: slug(bubble.parentTerm) || 'all',
                ancestries: ids(bubble.ancestries),
                traits: list(bubble.traits).filter(function(trait) {
                    if (!trait || !text(trait.id, 500) || traitIds.has(trait.id)) return false;
                    traitIds.add(trait.id);
                    return true;
                }).map(function(trait) {
                    return {id: trait.id, text: text(trait.text, 500) || trait.id};
                })
            }
        };
    }
    function parse(search) {
        var params = new URLSearchParams(search);
        var present = keys.some(function(key) { return params.has(key); });
        var warnings = [];
        if (String(search || '').length > maxURLLength ||
                (params.has('view') && params.get('view') !== '1')) {
            return {state: normalize(), present: true,
                warnings: ['This view link is too long or uses an unsupported version.']};
        }
        function split(key) { return (params.get(key) || '').split(',').filter(Boolean); }
        function json(key, fallback) {
            try { return JSON.parse(params.get(key) || JSON.stringify(fallback)); }
            catch (error) { warnings.push('Some settings in this view link could not be read.'); return fallback; }
        }
        var state = normalize({
            metric: params.get('metric'), stage: params.get('stage'),
            funders: split('funders'), cohorts: split('cohorts'),
            heatMap: {year: params.get('heatYear')},
            worldMap: {year: params.get('mapYear'), zoom: json('mapZoom', {})},
            doughnut: {year: params.get('doughnutYear'), parentTerm: params.get('doughnutTerm'),
                associations: params.get('associations') === '1'},
            timeSeries: {ancestry: params.get('ancestry'), notRecorded: params.get('notRecorded') === '1'},
            bubble: {parentTerm: params.get('bubbleTerm'), ancestries: split('bubbleAncestries'),
                traits: json('traits', [])}
        });
        return {state: state, present: present, warnings: warnings};
    }
    function url(state, base) {
        state = normalize(state);
        var result = new URL(base);
        keys.forEach(function(key) { result.searchParams.delete(key); });
        // Dataset binding is per page load, never a permanent shared-view setting.
        result.searchParams.delete('datasetId');
        function set(key, value) { if (value) result.searchParams.set(key, value); }
        set('view', '1');
        if (state.metric !== 'participants') set('metric', state.metric);
        if (state.stage !== 'discovery') set('stage', state.stage);
        set('funders', state.funders.join(',')); set('cohorts', state.cohorts.join(','));
        set('heatYear', state.heatMap.year); set('mapYear', state.worldMap.year);
        set('doughnutYear', state.doughnut.year);
        if (state.doughnut.parentTerm !== 'all') set('doughnutTerm', state.doughnut.parentTerm);
        if (state.doughnut.associations) set('associations', '1');
        if (state.timeSeries.ancestry !== 'all-ancestries') set('ancestry', state.timeSeries.ancestry);
        if (state.timeSeries.notRecorded) set('notRecorded', '1');
        if (state.bubble.parentTerm !== 'all') set('bubbleTerm', state.bubble.parentTerm);
        set('bubbleAncestries', state.bubble.ancestries.join(','));
        if (state.bubble.traits.length) set('traits', JSON.stringify(state.bubble.traits));
        if (state.worldMap.zoom.k !== 1 || state.worldMap.zoom.x || state.worldMap.zoom.y) {
            set('mapZoom', JSON.stringify(state.worldMap.zoom));
        }
        return result.href;
    }

    // The adapter keeps chart rendering separate from navigation. Revision checks
    // ensure a slow response cannot overwrite a later Back/Forward destination.
    function create(adapter, browser) {
        browser = browser || window;
        var ready = false, restoring = false, revision = 0, timer;
        var lastState, requested = parse(browser.location.search);
        function capture() { return normalize(adapter.capture()); }
        function announce(message) { adapter.announce(message || ''); }
        function sync(replace) {
            if (!ready || restoring) return;
            var state = capture(), serialized = JSON.stringify(state);
            if (serialized === lastState && !replace) return;
            var href = url(state, browser.location.href);
            if (href.length > maxURLLength) {
                announce('This selection is too large to share as a link. Reduce the selected traits.');
                return;
            }
            browser.history[replace ? 'replaceState' : 'pushState'](null, '', href);
            lastState = serialized;
            browser.dispatchEvent(new browser.CustomEvent('gwas:viewchange', {detail: state}));
        }
        function restore(parsed) {
            requested = parsed;
            if (!ready) return Promise.resolve(false);
            browser.clearTimeout(timer);
            var current = ++revision;
            restoring = true;
            announce('Restoring shared view…');
            return Promise.resolve(adapter.restore(parsed.state)).then(function(success) {
                if (current !== revision) return false;
                restoring = false;
                if (success === false) {
                    announce('This view could not be restored. Check the selection or try again.');
                    return false;
                }
                lastState = JSON.stringify(capture());
                if (parsed.present) sync(true);
                announce(parsed.warnings.join(' '));
                return true;
            }).catch(function(error) {
                if (current !== revision) return false;
                restoring = false;
                announce('This view could not be restored. Please try again.');
                if (browser.console) browser.console.error('Could not restore dashboard view', error);
                return false;
            });
        }
        browser.addEventListener('popstate', function() { restore(parse(browser.location.search)); });
        return {
            capture: capture,
            url: function() { return url(capture(), browser.location.href); },
            restore: function(state) { return restore({state: normalize(state), present: true, warnings: []}); },
            isRestoring: function() { return restoring; },
            isReady: function() { return ready; },
            ready: function() {
                if (ready) return;
                ready = true;
                if (requested.present) return restore(requested);
                lastState = JSON.stringify(capture());
                announce('');
                browser.dispatchEvent(new browser.CustomEvent('gwas:viewchange', {detail: capture()}));
                return Promise.resolve(true);
            },
            changed: function(delay) {
                if (!ready) return;
                if (restoring) { ++revision; restoring = false; announce(''); }
                browser.clearTimeout(timer);
                timer = browser.setTimeout(function() { sync(false); }, delay || 0);
            },
            settled: function() { sync(true); },
            copy: function() {
                if (browser.gwasProvenance && !browser.gwasProvenance.isCurrent()) {
                    announce(browser.gwasProvenance.message);
                    return Promise.resolve(false);
                }
                var href = url(capture(), browser.location.href);
                if (!ready || restoring || (adapter.canShare && !adapter.canShare()) || href.length > maxURLLength) {
                    announce(!ready || restoring || (adapter.canShare && !adapter.canShare()) ? 'Wait for the selected view to finish loading successfully before sharing.' :
                        'This selection is too large to share as a link. Reduce the selected traits.');
                    return Promise.resolve(false);
                }
                var copy = browser.navigator.clipboard && browser.navigator.clipboard.writeText;
                if (copy) {
                    return browser.navigator.clipboard.writeText(href).then(function() {
                        announce('Link copied.'); return true;
                    }).catch(function() { adapter.showLink(href); return false; });
                }
                adapter.showLink(href);
                return Promise.resolve(false);
            }
        };
    }
    return {normalize: normalize, parse: parse, url: url, create: create};
});
