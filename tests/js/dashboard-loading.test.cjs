'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const loading = require('../../app/static/js/dashboard-loading.js');
const help = require('../../app/static/js/dashboard-help.js');

function fixture() {
    let stale = false, calls = 0, parses = 0;
    const identifier = 'gwas-' + 'a'.repeat(64);
    const response = (status = 200, id = identifier, data = {value: 1}) => ({status,
        headers: {get: () => id}, json: async () => { parses += 1; return data; }});
    const browser = {gwasLoadedProvenance: {datasetId: identifier}, gwasProvenance: {
        markStale() { stale = true; }, isCurrent: () => !stale
    }, fetch: async () => { calls += 1; return response(); }};
    const bootstrap = {urls: {plot: '/plot?datasetId=' + identifier}, requests: {}};
    return {browser, bootstrap, response, counts: () => ({stale, calls, parses})};
}

test('eager responses remain unparsed until consumed; success is cached', async () => {
    const f = fixture();
    f.bootstrap.requests.plot = Promise.resolve({response: f.response()});
    const api = loading.create(f.browser, f.bootstrap);
    assert.equal(f.counts().parses, 0);
    assert.deepEqual(await api.load('plot'), {value: 1});
    await api.load('plot', true);
    assert.deepEqual(f.counts(), {stale: false, calls: 0, parses: 1});
});

test('network failures are visible rejections; only explicit retry starts a new request', async () => {
    const f = fixture();
    f.bootstrap.requests.plot = Promise.resolve({error: new Error('offline')});
    const api = loading.create(f.browser, f.bootstrap);
    await assert.rejects(api.load('plot'), /offline/);
    assert.equal(f.counts().calls, 0);
    assert.deepEqual(await api.load('plot', true), {value: 1});
    assert.equal(f.counts().calls, 1);
});

test('missing and mismatched successful-response headers fail closed before JSON parse', async () => {
    for (const id of [null, 'different']) {
        const f = fixture();
        f.bootstrap.requests.plot = Promise.resolve({response: f.response(200, id)});
        await assert.rejects(loading.create(f.browser, f.bootstrap).load('plot'), error => error.status === 409);
        assert.deepEqual(f.counts(), {stale: true, calls: 0, parses: 0});
    }
});

test('retry responses get the same strict header verification as eager requests', async () => {
    const f = fixture();
    f.browser.fetch = async () => f.response(200, null);
    await assert.rejects(loading.create(f.browser, f.bootstrap).load('plot', true), error => error.status === 409);
    assert.equal(f.counts().parses, 0);
});

test('HTTP failures and malformed JSON can be retried without marking the dataset stale', async () => {
    for (const bad of [{status: 503, headers: {get: () => null}}, {
        status: 200, headers: {get: () => 'gwas-' + 'a'.repeat(64)}, json: async () => { throw new Error('Malformed JSON'); }
    }]) {
        const f = fixture();
        f.bootstrap.requests.plot = Promise.resolve({response: bad});
        const api = loading.create(f.browser, f.bootstrap);
        await assert.rejects(api.load('plot'));
        assert.equal(f.counts().stale, false);
        assert.deepEqual(await api.load('plot', true), {value: 1});
    }
});

test('duplicate requests share a promise and dependencies keep their successful cached data', async () => {
    const f = fixture();
    const api = loading.create(f.browser, f.bootstrap);
    const first = api.load('plot');
    assert.equal(api.load('plot', true), first);
    await first;
    assert.equal(f.counts().calls, 1);
});

test('XHR compatibility path verifies response identity before parsing too', async () => {
    const f = fixture();
    delete f.browser.fetch;
    f.browser.XMLHttpRequest = class {
        open() {} getResponseHeader() { return null; }
        send() { this.status = 200; this.responseText = '{bad JSON'; this.onload(); }
    };
    await assert.rejects(loading.create(f.browser, f.bootstrap).load('plot'), error => error.status === 409);
    assert.equal(f.counts().stale, true);
});

test('examples come from available metadata with recorded studies, never invented IDs', () => {
    assert.equal(help.pickExample({results: []}), undefined);
    const good = {id: 'recorded', text: 'Recorded entity', studyCount: 2};
    assert.equal(help.pickExample({results: [{id: 'empty', text: 'Empty', studyCount: 0}, good]}), good);
});
