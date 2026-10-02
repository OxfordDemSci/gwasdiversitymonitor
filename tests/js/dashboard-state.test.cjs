const assert = require('node:assert/strict');
const test = require('node:test');
const state = require('../../app/static/js/dashboard-state.js');

function fixture(search = '') {
    const listeners = {}, history = [], messages = [], restores = [];
    const browser = {
        location: new URL('https://example.org/' + search),
        history: Object.fromEntries(['pushState', 'replaceState'].map(kind => [kind, (_, title, href) => {
            browser.location = new URL(href); history.push({kind, href});
        }])),
        setTimeout, clearTimeout,
        addEventListener: (name, fn) => { listeners[name] = fn; },
        dispatchEvent: () => {},
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        navigator: {},
        console
    };
    let view = state.normalize();
    const adapter = {
        capture: () => view,
        restore: value => { restores.push(value); view = value; return Promise.resolve(true); },
        announce: message => messages.push(message),
        showLink: href => messages.push(href)
    };
    return {browser, adapter, listeners, history, messages, restores,
        setView: value => { view = state.normalize(value); }};
}

test('round trips the complete research view without removing unrelated URL fields', () => {
    const original = state.normalize({
        metric: 'studies', stage: 'replication', funders: ['wellcome', 'nih'], cohorts: ['uk-biobank'],
        heatMap: {year: '2010'}, worldMap: {year: '2020', zoom: {x: -31.5, y: 7, k: 2}},
        doughnut: {year: '2019', parentTerm: 'metabolic-disease', associations: true},
        timeSeries: {ancestry: 'asian', notRecorded: true},
        bubble: {parentTerm: 'body-measurement', ancestries: ['asian', 'european'],
            traits: [{id: 'trait-<50-&-β', text: 'Trait <50 & β'}]}
    });
    const url = new URL(state.url(original, 'https://example.org/?utm_source=paper#dashboard'));
    assert.equal(url.searchParams.get('utm_source'), 'paper');
    assert.equal(url.hash, '#dashboard');
    assert.deepEqual(state.parse(url.search).state, original);
    assert.equal(state.url(original, url.href), url.href);
});

test('bounds and validates untrusted values without coercing IDs', () => {
    const result = state.normalize({
        metric: 'wrong', stage: '<script>', funders: [123, ['nih'], 'nih', 'nih', '../bad'],
        cohorts: Array.from({length: 100}, (_, index) => 'c-' + index),
        heatMap: {year: 'tomorrow'}, worldMap: {zoom: {k: Infinity, x: 100001, y: '4'}},
        bubble: {traits: [{id: 'valid', text: '<script>alert(1)</script>'}, {id: 'valid'}, {id: ['bad']}, {id: '\n'}]}
    });
    assert.equal(result.metric, 'participants');
    assert.equal(result.stage, 'discovery');
    assert.deepEqual(result.funders, ['nih']);
    assert.equal(result.cohorts.length, 50);
    assert.equal(result.heatMap.year, null);
    assert.deepEqual(result.worldMap.zoom, {x: 0, y: 0, k: 1});
    assert.deepEqual(result.bubble.traits, [{id: 'valid', text: '<script>alert(1)</script>'}]);
    assert.equal(state.parse('?view=2&metric=studies').state.metric, 'participants');
    assert.equal(state.parse('?view=2').warnings.length, 1);
    assert.equal(state.parse('?traits=%7B').warnings.length, 1);
    assert.equal(state.parse('?view=1&traits=' + 'x'.repeat(25000)).warnings.length, 1);
});

test('shareable views never pin an incidental dataset version', () => {
    const url = new URL(state.url({funders: ['nih']}, 'https://example.org/?datasetId=gwas-old&utm_source=paper'));
    assert.equal(url.searchParams.has('datasetId'), false);
    assert.equal(url.searchParams.get('funders'), 'nih');
    assert.equal(url.searchParams.get('utm_source'), 'paper');
});

test('restoration waits for baseline data and ready cannot start duplicate requests', async () => {
    const f = fixture('?view=1&metric=studies&funders=nih');
    const controller = state.create(f.adapter, f.browser);
    assert.equal(await controller.copy(), false);
    assert.equal(f.restores.length, 0);
    await controller.ready();
    await controller.ready();
    assert.equal(f.restores.length, 1);
    assert.deepEqual(f.restores[0].funders, ['nih']);
    assert.equal(f.history.length, 1);
    assert.equal(f.history[0].kind, 'replaceState');
});

test('coalesces interaction events and creates only distinct Back/Forward entries', async () => {
    const f = fixture();
    const controller = state.create(f.adapter, f.browser);
    await controller.ready();
    assert.equal(f.history.length, 0);
    f.setView({metric: 'studies'});
    controller.changed(); controller.changed();
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(f.history.length, 1);
    assert.equal(f.history[0].kind, 'pushState');
    controller.changed();
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(f.history.length, 1);
    f.browser.location = new URL('https://example.org/');
    f.listeners.popstate();
    await Promise.resolve();
    assert.equal(controller.capture().metric, 'participants');
    assert.equal(f.history.length, 1);
});

test('newest navigation owns completion and ignores stale restore failures', async () => {
    const f = fixture();
    const pending = [];
    f.adapter.restore = value => { f.setView(value); return new Promise(resolve => pending.push(resolve)); };
    const controller = state.create(f.adapter, f.browser);
    await controller.ready();
    const first = controller.restore({funders: ['nih']});
    const second = controller.restore({cohorts: ['uk-biobank']});
    pending[1](true);
    assert.equal(await second, true);
    pending[0](false);
    assert.equal(await first, false);
    assert.equal(f.history.length, 1);
    assert.equal(f.messages.at(-1), '');
    assert.deepEqual(controller.capture().cohorts, ['uk-biobank']);
});

test('does not silently rewrite a failed link and allows correction after failure', async () => {
    const f = fixture('?view=1&funders=unknown');
    f.adapter.restore = () => Promise.resolve(false);
    const controller = state.create(f.adapter, f.browser);
    assert.equal(await controller.ready(), false);
    assert.equal(f.history.length, 0);
    assert.match(f.messages.at(-1), /could not be restored/);
    f.setView({funders: ['nih']});
    controller.changed();
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(new URL(f.history[0].href).searchParams.get('funders'), 'nih');
});

test('clipboard failure exposes a manually copyable URL', async () => {
    const f = fixture();
    f.browser.navigator.clipboard = {writeText: () => Promise.reject(new Error('denied'))};
    const controller = state.create(f.adapter, f.browser);
    await controller.ready();
    assert.equal(await controller.copy(), false);
    assert.match(f.messages.at(-1), /^https:\/\/example.org\/\?view=1/);
});

test('public copy API cannot share a view marked as a mixed dataset', async () => {
    const f = fixture();
    f.browser.gwasProvenance = {isCurrent: () => false, message: 'Reload the changed dataset.'};
    let copied = false;
    f.browser.navigator.clipboard = {writeText: async () => { copied = true; }};
    f.adapter.showLink = () => { throw new Error('The manual fallback must also be blocked'); };
    const controller = state.create(f.adapter, f.browser);
    await controller.ready();
    assert.equal(await controller.copy(), false);
    assert.equal(copied, false);
    assert.equal(f.messages.at(-1), 'Reload the changed dataset.');
});
