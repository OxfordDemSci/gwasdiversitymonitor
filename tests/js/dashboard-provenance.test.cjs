'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const provenance = require('../../app/static/js/dashboard-provenance.js');

const datasetId = 'gwas-' + 'a'.repeat(64);

function browserFixture() {
    const events = [], listeners = {}, navigation = [];
    const alert = {hidden: true};
    const button = {tagName: 'BUTTON', attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }};
    const timestamps = Object.fromEntries(['generatedAt', 'lastSuccessfulFetchAt', 'lastSuccessfulRunAt']
        .map(key => [key, {textContent: ''}]));
    const browser = {
        location: {href: 'https://example.org/?view=1&funders=old&datasetId=obsolete', assign: href => navigation.push(href)},
        document: {
            readyState: 'complete',
            getElementById: id => id === 'dashboard-dataset-stale' ? alert : null,
            querySelectorAll: () => [button],
            querySelector: selector => timestamps[selector.match(/="(.*?)"/)[1]],
            addEventListener: (name, listener) => { listeners[name] = listener; }
        },
        dispatchEvent: event => events.push(event),
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }
    };
    return {browser, alert, button, events, listeners, timestamps, navigation};
}

function coverageFixture() {
    const f = browserFixture();
    const ids = ['dashboard-provenance', 'provenance-coverage-status', 'provenance-coverage-retry',
        'provenance-coverage-values', 'provenance-funder-coverage', 'provenance-cohort-coverage'];
    const nodes = Object.fromEntries(ids.map(id => [id, {hidden: true, textContent: '', listeners: {},
        addEventListener(name, fn) { this.listeners[name] = fn; }}]));
    const original = f.browser.document.getElementById;
    f.browser.document.getElementById = id => nodes[id] || original(id);
    f.nodes = nodes;
    f.open = async () => {
        nodes['dashboard-provenance'].open = true;
        nodes['dashboard-provenance'].listeners.toggle();
        await new Promise(resolve => setImmediate(resolve));
    };
    return f;
}

test('snapshot identity and sources stay fixed as callers prepare exports', () => {
    const source = {datasetId, sources: [{filename: 'catalog-r2026-10-02.tsv'}]};
    const api = provenance.create(source);
    source.datasetId = 'new';
    source.sources[0].filename = 'changed';
    const exported = api.capture();
    exported.sources[0].filename = 'also changed';
    assert.equal(api.loaded.datasetId, datasetId);
    assert.equal(api.capture().sources[0].filename, 'catalog-r2026-10-02.tsv');
    assert.throws(() => { api.loaded = {}; }, TypeError);
    assert.throws(() => { api.loaded.sources.push({}); }, TypeError);
    assert.equal(api.isCurrent(datasetId), true);
    assert.equal(api.isCurrent('gwas-' + 'b'.repeat(64)), false);
    assert.equal(api.isCurrent(null), false);
    assert.equal(api.isCurrent(undefined), false);
    assert.equal(api.isCurrent(), true);
});

test('a stale response permanently prevents export and announces reload once', () => {
    const f = browserFixture();
    const api = provenance.create({datasetId}, f.browser);
    api.initialise();
    assert.equal(f.alert.hidden, true);
    api.markStale(); api.markStale();
    assert.equal(api.isCurrent(), false);
    assert.equal(api.isCurrent(datasetId), false);
    assert.equal(f.alert.hidden, false);
    assert.equal(f.button.disabled, true);
    assert.equal(f.button.attributes['aria-disabled'], 'true');
    assert.equal(f.events.length, 1);
    assert.equal(f.events[0].type, 'gwas:datasetstale');
    let prevented = false, stopped = false;
    f.listeners.click({target: {closest: () => f.button},
        preventDefault: () => { prevented = true; }, stopImmediatePropagation: () => { stopped = true; }});
    assert.equal(prevented && stopped, true);
    assert.equal(api.capture().datasetId, datasetId);
});

test('successful checks, generation, and run completion retain separate UTC values', () => {
    const f = browserFixture();
    const api = provenance.create({datasetId, generatedAt: '2026-09-29T23:40:00+00:00',
        lastSuccessfulFetchAt: '2026-10-02T01:10:00Z', lastSuccessfulRunAt: null}, f.browser);
    api.initialise();
    assert.equal(f.timestamps.generatedAt.textContent, '29 Sept 2026, 23:40 UTC');
    assert.equal(f.timestamps.lastSuccessfulFetchAt.textContent, '2 Oct 2026, 01:10 UTC');
    assert.equal(f.timestamps.lastSuccessfulRunAt.textContent, 'Not recorded');
    assert.equal(provenance.formatTimestamp('nonsense'), 'Not recorded');
    assert.equal(provenance.formatTimestamp('2026-10-02T01:10:00'), 'Not recorded');
});

test('reload preserves current settings and removes an incidental dataset binding', () => {
    const f = browserFixture();
    f.browser.gwasDashboardState = {isReady: () => true, isRestoring: () => false,
        url: () => 'https://example.org/?view=1&funders=new&metric=studies&datasetId=obsolete'};
    const api = provenance.create({datasetId}, f.browser);
    api.markStale(); api.reload();
    const url = new URL(f.navigation[0]);
    assert.equal(url.searchParams.get('funders'), 'new');
    assert.equal(url.searchParams.get('metric'), 'studies');
    assert.equal(url.searchParams.has('datasetId'), false);
});

test('reload during initial restoration keeps the requested shared view', () => {
    const f = browserFixture();
    f.browser.gwasDashboardState = {isReady: () => true, isRestoring: () => true,
        url: () => { throw new Error('An incomplete view must not replace the requested settings'); }};
    const api = provenance.create({}, f.browser);
    assert.equal(api.isCurrent(), true);
    api.reload();
    assert.equal(new URL(f.navigation[0]).searchParams.get('funders'), 'old');
    assert.equal(new URL(f.navigation[0]).searchParams.has('datasetId'), false);
});

test('metadata coverage loads only on opening, binds the snapshot, and reuses its result', async () => {
    const f = coverageFixture();
    const requests = [];
    f.browser.fetch = async url => {
        requests.push(url);
        return {ok: true, json: async () => ({datasetId, coverage: {
            study_count: 200, funder_linked_study_count: 110, cohort_linked_study_count: 23
        }})};
    };
    const api = provenance.create({datasetId}, f.browser);
    api.initialise();
    assert.equal(requests.length, 0);
    await f.open();
    assert.equal(requests.length, 1);
    const url = new URL(requests[0], 'https://example.org');
    assert.equal(url.searchParams.get('coverage'), '1');
    assert.equal(url.searchParams.get('datasetId'), datasetId);
    assert.equal(f.nodes['provenance-funder-coverage'].textContent, '55% (110 of 200)');
    assert.equal(f.nodes['provenance-cohort-coverage'].textContent, '11.5% (23 of 200)');
    assert.equal(f.nodes['provenance-coverage-values'].hidden, false);
    await f.open();
    assert.equal(requests.length, 1);
});

test('coverage can retry a transient error but cannot mix metadata from another dataset', async () => {
    const f = coverageFixture();
    let calls = 0;
    f.browser.fetch = async () => {
        calls += 1;
        if (calls === 1) throw new Error('offline');
        return {ok: true, json: async () => ({datasetId: 'gwas-' + 'b'.repeat(64), coverage: {
            study_count: 10, funder_linked_study_count: 5, cohort_linked_study_count: 1
        }})};
    };
    const api = provenance.create({datasetId}, f.browser);
    api.initialise();
    await f.open();
    assert.equal(f.nodes['provenance-coverage-retry'].hidden, false);
    await f.nodes['provenance-coverage-retry'].listeners.click();
    assert.equal(api.isCurrent(), false);
    assert.equal(f.nodes['provenance-coverage-values'].hidden, true);
    assert.equal(f.nodes['provenance-coverage-retry'].hidden, true);
    assert.match(f.nodes['provenance-coverage-status'].textContent, /dataset changed/);
    assert.equal(f.alert.hidden, false);
});
