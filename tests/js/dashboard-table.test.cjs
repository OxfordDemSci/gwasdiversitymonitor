'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {create, pageRows, PAGE_SIZE} = require('../../app/static/js/dashboard-table.js');

function fixture() {
    const events = {}, calls = [];
    let ready = true, current = true;
    class Element {
        constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.isConnected = true; }
        addEventListener(name, handler) { this.listeners[name] = handler; }
        setAttribute(name, value) { this.attributes[name] = value; }
        appendChild(child) { this.children.push(child); }
        replaceChildren(...children) {
            this.children = children.flatMap(child => child.tagName === 'fragment' ? child.children : [child]);
        }
        focus() { document.activeElement = this; }
        showModal() { this.open = true; }
        close() { this.open = false; this.listeners.close(); }
        click() { return this.listeners.click({}); }
    }
    const ids = ['dialog', 'title', 'context', 'methodology', 'caption', 'head', 'body', 'range',
        'status', 'previous', 'next', 'refresh', 'close', 'export'];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element(id)]));
    const button = new Element('button');
    const document = {
        activeElement: button, getElementById: id => nodes[id.replace('dashboard-table-', '')],
        createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment'),
        querySelectorAll: () => [button], addEventListener: () => {},
    };
    const snapshot = {id: 'bubbleGraph', title: 'Bubbles', rowCount: 121,
        columns: [{key: 'text', label: 'Trait', type: 'text'}, {key: 'count', label: 'Count', type: 'number'}],
        settings: {stage: 'initial'}, provenance: {datasetId: 'gwas-' + 'a'.repeat(64)},
        methodology: ['Participant instances, not unique people.'],
        rowAt: index => { calls.push(index); return {text: '<script>' + index + '</script>', count: index}; },
    };
    const registry = {
        canExport: () => ready,
        snapshot: () => { if (!ready) throw Error('loading'); current = true; return snapshot; },
        assertCurrent: () => { if (!ready || !current) throw Error('changed'); },
        reportError: message => { nodes.status.textContent = message; },
        loadExporter: async () => ({downloadSnapshots: async captured => { assert.equal(captured[0], snapshot); }}),
    };
    const browser = {document, gwasChartData: registry, addEventListener: (name, fn) => { events[name] = fn; }};
    return {api: create(browser), nodes, button, document, calls, snapshot, events, registry,
        ready: value => { ready = value; }, current: value => { current = value; }};
}

test('huge lazy providers read only the requested page and clamp bounds', () => {
    const calls = [];
    const snapshot = {rowCount: 200001, rowAt: index => { calls.push(index); return index; }};
    const page = pageRows(snapshot, 20);
    assert.equal(PAGE_SIZE, 50);
    assert.equal(page.rows.length, 50);
    assert.equal(calls.length, 50);
    assert.equal(page.rows[0], 1000);
    assert.deepEqual(pageRows(snapshot, 999999).rows, [200000]);
    assert.equal(pageRows(snapshot, -4).start, 0);
    assert.equal(pageRows({rowCount: 0, rowAt: () => { throw Error('must not read'); }}, 0).rows.length, 0);
});

test('opening and pagination use exact text-only cells and labelled headers', () => {
    const f = fixture();
    assert.equal(f.calls.length, 0);
    assert.equal(f.api.open('bubbleGraph', f.button), true);
    assert.equal(f.nodes.dialog.open, true);
    assert.equal(f.document.activeElement, f.nodes.close);
    assert.equal(f.calls.length, 50);
    assert.equal(f.nodes.body.children[0].children[0].textContent, '<script>0</script>');
    assert.equal(f.nodes.head.children[0].children[0].attributes.scope, 'col');
    assert.equal(f.nodes.previous.disabled, true);
    f.nodes.next.click();
    assert.equal(f.calls[50], 50);
    assert.match(f.nodes.range.textContent, /Rows 51–100 of 121/);
    f.nodes.next.click();
    assert.equal(f.nodes.body.children.length, 21);
    assert.equal(f.nodes.next.disabled, true);
    f.api.close();
    assert.equal(f.document.activeElement, f.button);
    assert.equal(f.nodes.body.children.length, 0);
});

test('provider/view changes remove stale rows and permit explicit refresh only when ready', () => {
    const f = fixture(); f.api.open('bubbleGraph', f.button);
    f.current(false); f.ready(false); f.events['gwas:chartdatachanged']();
    assert.equal(f.nodes.body.children.length, 0);
    assert.equal(f.nodes.next.disabled, true);
    assert.equal(f.nodes.export.disabled, true);
    assert.equal(f.nodes.refresh.disabled, true);
    assert.match(f.nodes.status.textContent, /view changed/);
    f.ready(true); f.events['gwas:viewchange']();
    assert.equal(f.nodes.refresh.disabled, false);
    assert.equal(f.nodes.body.children.length, 0);
    f.api.refresh();
    assert.equal(f.nodes.body.children.length, 50);
});

test('loading failures guard opening and table export retains its captured snapshot', async () => {
    const f = fixture(); f.ready(false);
    assert.equal(f.api.open('bubbleGraph', f.button), false);
    assert.equal(f.calls.length, 0);
    assert.match(f.nodes.status.textContent, /finish loading/);
    f.ready(true); f.api.open('bubbleGraph', f.button);
    await f.nodes.export.click();
    assert.match(f.nodes.status.textContent, /downloaded/);
    let release;
    f.registry.loadExporter = () => new Promise(resolve => { release = resolve; });
    const pending = f.nodes.export.click();
    f.current(false);
    release({downloadSnapshots: () => { throw Error('must not download'); }});
    await pending;
    assert.equal(f.nodes.status.textContent, 'changed');
});
