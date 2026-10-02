'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../app/static/js/script.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
    const calls = [], errors = [], scripts = [], images = [], revoked = [];
    let revision = 1, encode;
    const snapshot = Object.freeze({id: 'bubbleGraph', revision,
        provenance: {datasetId: 'gwas-' + 'a'.repeat(64)}, exportedAt: '2026-10-03T10:20:30Z',
        view: {funders: ['wellcome'], cohorts: []}, settings: {stage: 'initial', metric: 'participants'}});
    const exporter = {downloadSnapshots: async captures => { calls.push(['bundle', captures[0]]); return true; }};
    const registry = {
        snapshot(id) { calls.push(['snapshot', id]); return snapshot; },
        assertCurrent(capture) { calls.push(['guard', capture]); if (revision !== capture.revision) throw new Error('The view changed; retry.'); },
        prepareImage(id) { calls.push(['prepare', id]); },
        loadExporter: async () => exporter,
        reportError: message => errors.push(message)
    };
    const canvas = {getContext: () => ({drawImage: () => calls.push(['draw'])}), toBlob(callback) { encode = callback; }};
    const document = {
        createElement: () => ({remove() { this.removed = true; }}),
        head: {appendChild: script => scripts.push(script)},
        getElementById: id => id === 'downloadCanvas' ? canvas : null
    };
    const browser = {gwasChartData: registry, gwasStaticAssets: {d3plusText: '/text.js'}};
    const jquery = {find() { return this; }, click() {}, scroll() {}};
    const context = vm.createContext({window: browser, document, $: () => jquery, Promise, Blob,
        URL: {createObjectURL: () => 'blob:figure', revokeObjectURL: url => revoked.push(url)},
        Image: class { constructor() { images.push(this); } }
    });
    vm.runInContext(source, context);
    context.renderDownloadImage = async (selector, svg, png, capture, module) => {
        calls.push(['render', capture]);
        return module.downloadSnapshots([capture]);
    };
    return {context, browser, registry, exporter, snapshot, calls, errors, scripts, images, revoked,
        mutate() { revision += 1; }, encode: blob => encode(blob)};
}

test('capture precedes lazy dependencies and canvas refresh occurs at actual capture', async () => {
    const f = fixture();
    const result = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    assert.equal(f.calls[0][0], 'snapshot');
    assert.equal(f.calls.some(call => call[0] === 'prepare'), false);
    f.browser.d3plus = {TextBox: function() {}};
    f.scripts[0].onload();
    assert.equal(await result, true);
    assert.deepEqual(f.calls.filter(call => ['prepare', 'render', 'bundle'].includes(call[0])).map(call => call[0]),
        ['prepare', 'render', 'bundle']);
    assert.equal(f.calls.find(call => call[0] === 'bundle')[1], f.snapshot);
});

test('a changed view while loading the text library never renders or saves', async () => {
    const f = fixture();
    const result = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    f.mutate();
    f.browser.d3plus = {TextBox: function() {}};
    f.scripts[0].onload();
    await assert.rejects(result, /view changed/);
    assert.equal(f.calls.some(call => call[0] === 'render'), false);
    assert.match(f.errors.at(-1), /retry/i);
});

test('a changed view while loading the exporter also prevents capture', async () => {
    const f = fixture();
    f.browser.d3plus = {TextBox: function() {}};
    let finish;
    f.registry.loadExporter = () => new Promise(resolve => { finish = resolve; });
    const result = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    await tick();
    f.mutate(); finish(f.exporter);
    await assert.rejects(result, /view changed/);
    assert.equal(f.calls.some(call => call[0] === 'prepare'), false);
});

test('a failed lazy library download can be retried without reloading the page', async () => {
    const f = fixture();
    const failed = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    f.scripts[0].onerror();
    await assert.rejects(failed, /could not be loaded/);
    assert.equal(f.scripts[0].removed, true);
    const retry = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    assert.equal(f.scripts.length, 2);
    f.browser.d3plus = {TextBox: function() {}};
    f.scripts[1].onload();
    assert.equal(await retry, true);
});

test('only one image export may use the shared shell and canvas at a time', async () => {
    const f = fixture();
    const first = f.context.downloadImage('#bubbleGraph', 'bubbleSVG', false);
    await assert.rejects(f.context.downloadImage('#bubbleGraph', 'bubbleSVG', true), /Please wait/);
    assert.equal(f.calls.filter(call => call[0] === 'snapshot').length, 1);
    f.browser.d3plus = {TextBox: function() {}};
    f.scripts[0].onload();
    assert.equal(await first, true);
    assert.equal(await f.context.downloadImage('#bubbleGraph', 'bubbleSVG', true), true);
});

test('PNG decoding refuses a changed view and releases its SVG URL', async () => {
    const f = fixture();
    const result = f.context.rasterizeExportSvg(new Blob(['svg']), 600, 800, f.snapshot);
    f.mutate(); f.images[0].onload();
    await assert.rejects(result, /view changed/);
    assert.equal(f.calls.some(call => call[0] === 'draw'), false);
    assert.deepEqual(f.revoked, ['blob:figure']);
});

test('PNG encoding rechecks after its asynchronous canvas callback', async () => {
    const f = fixture();
    const result = f.context.rasterizeExportSvg(new Blob(['svg']), 600, 800, f.snapshot);
    f.images[0].onload();
    assert.equal(f.calls.some(call => call[0] === 'draw'), true);
    f.mutate(); f.encode(new Blob(['png']));
    await assert.rejects(result, /view changed/);
});

test('PNG conversion failures are actionable and release temporary URLs', async () => {
    const f = fixture();
    const result = f.context.rasterizeExportSvg(new Blob(['svg']), 600, 800, f.snapshot);
    f.images[0].onerror();
    await assert.rejects(result, /Try SVG instead/);
    assert.deepEqual(f.revoked, ['blob:figure']);
});

test('footer carries the frozen dataset, selection, chart settings, citation and raster disclosure', () => {
    const f = fixture();
    const footer = f.context.imageExportFooter(f.snapshot, {citation: 'Paper citation', image: {embeddedRaster: true}}).join('\n');
    assert.match(footer, /Dataset: gwas-aaaaaaaaaaaa/);
    assert.match(footer, /Funders: wellcome; cohorts: all/);
    assert.match(footer, /stage: initial; metric: participants/);
    assert.match(footer, /Paper citation/);
    assert.match(footer, /not entirely vector/);
});
