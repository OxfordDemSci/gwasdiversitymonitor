const assert = require('node:assert/strict');
const test = require('node:test');
const createAccessibility = require('../../app/static/js/dashboard-accessibility.js');

function setup(count = 3) {
    const ids = new Map();
    const handlers = new Map();
    const documentEvents = new Map();
    const windowEvents = new Map();
    let document;
    class Element {
        constructor() {
            this.attributes = new Map();
            this.style = {};
            this.hidden = false;
            this.classList = {add() {}};
            this.children = [];
            this.textContent = '';
        }
        set innerHTML(value) { throw new Error('Chart labels must use text, not HTML: ' + value); }
        setAttribute(key, value) { this.attributes.set(key, String(value)); }
        getAttribute(key) { return this.attributes.get(key); }
        appendChild(child) { this.children.push(child); if (child.id) ids.set(child.id, child); }
        getBoundingClientRect() { return {left: 20, top: 200, bottom: 220, width: 100, height: 20}; }
        focus() {
            document.activeElement = this;
            if (handlers.has('focus.chartAccessibility')) handlers.get('focus.chartAccessibility').call(this, this.__data__);
        }
    }
    const chart = new Element();
    ids.set('testChart', chart);
    document = {
        body: new Element(), activeElement: null,
        createElement: () => new Element(),
        getElementById: id => ids.get(id),
        querySelectorAll: () => [],
        addEventListener: (name, callback) => documentEvents.set(name, callback)
    };
    const browser = {
        document, innerWidth: 400, innerHeight: 700, d3: {},
        addEventListener: (name, callback) => windowEvents.set(name, callback)
    };
    const nodes = Array.from({length: count}, (_, index) => {
        const node = new Element(); node.__data__ = {label: 'Record ' + (index + 1)}; return node;
    });
    const selection = {
        nodes: () => nodes,
        on(name, handler) { handlers.set(name, handler); return this; }
    };
    const api = createAccessibility(browser);
    api.enhanceMarks('testChart', selection, data => data.label);
    function key(node, key, extra = {}) {
        let prevented = false;
        browser.d3.event = {...extra, key, preventDefault() { prevented = true; }};
        handlers.get('keydown.chartAccessibility').call(node, node.__data__);
        return prevented;
    }
    return {api, ids, document, browser, chart, nodes, handlers, documentEvents, windowEvents, key};
}

test('one keyboard stop per chart supports arrows, Home and End without trapping Tab', () => {
    const fixture = setup(120);
    const {nodes, document, key} = fixture;
    const tabbable = () => nodes.filter(node => node.getAttribute('tabindex') === '0');
    assert.deepEqual(tabbable(), [nodes[0]]);
    assert.equal(key(nodes[0], 'ArrowRight'), true);
    assert.equal(document.activeElement, nodes[1]);
    assert.deepEqual(tabbable(), [nodes[1]]);
    key(nodes[1], 'End');
    assert.equal(document.activeElement, nodes[119]);
    key(nodes[119], 'Home');
    assert.equal(document.activeElement, nodes[0]);
    assert.equal(key(nodes[0], 'Tab'), false);
    assert.equal(key(nodes[0], 'ArrowRight', {ctrlKey: true}), false);
    assert.equal(document.activeElement, nodes[0]);
});

test('first focus followed by tap/click leaves the tooltip visible with literal label text', () => {
    const {nodes, ids, handlers} = setup();
    nodes[0].__data__.label = '<img src=x onerror=alert(1)>: 12.345%';
    nodes[0].focus();
    handlers.get('click.chartAccessibility').call(nodes[0], nodes[0].__data__);
    const tooltip = ids.get('gwas-chart-tooltip');
    assert.equal(tooltip.hidden, false);
    assert.equal(tooltip.textContent, '<img src=x onerror=alert(1)>: 12.345%');
    assert.equal(tooltip.style.left, '20px');
});

test('Escape, outside touch, blur and chart redraw dismiss tooltips', () => {
    const {nodes, ids, key, handlers, documentEvents, windowEvents} = setup();
    nodes[0].focus();
    key(nodes[0], 'Escape');
    assert.equal(ids.get('gwas-chart-tooltip').hidden, true);
    key(nodes[0], 'Enter');
    assert.equal(ids.get('gwas-chart-tooltip').hidden, false);
    documentEvents.get('pointerdown')({target: {}});
    assert.equal(ids.get('gwas-chart-tooltip').hidden, true);
    nodes[1].focus();
    handlers.get('blur.chartAccessibility')();
    assert.equal(ids.get('gwas-chart-tooltip').hidden, true);
    nodes[2].focus();
    windowEvents.get('gwas:chartdatachanged')();
    assert.equal(ids.get('gwas-chart-tooltip').hidden, true);
});

test('empty charts add no mark focus stops or tooltip elements', () => {
    const {chart, ids, handlers} = setup(0);
    assert.equal(chart.children.length, 0);
    assert.equal(ids.has('gwas-chart-tooltip'), false);
    assert.equal(handlers.size, 0);
});
