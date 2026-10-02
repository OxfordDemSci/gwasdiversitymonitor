const test = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../../app/static/js/dashboard-chart-data.js');
const exportsApi = require('../../app/static/js/dashboard-export.js');

function fixture() {
    let view = {funders: [], cohorts: [], metric: 'participants', stage: 'discovery'};
    let restoring = false;
    const browser = {
        gwasDashboardState: {capture: () => structuredClone(view), isReady: () => true,
            isRestoring: () => restoring, url: () => 'https://example.test/?view=1'},
        gwasProvenance: {loaded: {datasetId: 'gwas-' + 'a'.repeat(64)}, isCurrent: () => true,
            capture: () => ({datasetId: 'gwas-' + 'a'.repeat(64)})},
        document: {querySelector: () => null},
    };
    const registry = create(browser);
    let calls = 0;
    let rows = [{label: 'European', count: 10}];
    const provider = () => {
        calls++;
        const captured = rows;
        return {title: 'Chart', columns: [{key: 'label', label: 'Label', type: 'text'},
            {key: 'count', label: 'Count', type: 'number'}], rowCount: captured.length,
            rowAt: index => captured[index], settings: {year: 2020}, methodology: ['Recorded ancestry denominator.']};
    };
    registry.chartIds.forEach(id => registry.register(id, provider));
    return {registry, browser, calls: () => calls, view: value => { view = value; },
        rows: value => { rows = value; }, restoring: value => { restoring = value; }};
}

test('registration is lazy and snapshots retain selected rows and frozen metadata', () => {
    const f = fixture();
    assert.equal(f.calls(), 0);
    assert.equal(f.registry.canExport(), false);
    f.registry.setSelectionReady(true);
    const snapshot = f.registry.snapshot('bubbleGraph');
    assert.equal(f.calls(), 1);
    f.rows([{label: 'Asian', count: 30}]);
    assert.deepEqual(snapshot.rowAt(0), {label: 'European', count: 10});
    assert.ok(Object.isFrozen(snapshot.provenance));
    assert.ok(Object.isFrozen(snapshot.settings));
    assert.throws(() => snapshot.rowAt(1), RangeError);
});

test('pending, failed, restored, or changed selections cannot export old charts', () => {
    const f = fixture();
    f.registry.setSelectionReady(true);
    const snapshot = f.registry.snapshot('heatMap');
    f.restoring(true);
    assert.throws(() => f.registry.snapshot('heatMap'), /finish loading/);
    f.restoring(false);
    f.view({funders: ['new'], cohorts: [], metric: 'participants', stage: 'discovery'});
    assert.equal(f.registry.canExport(), false);
    f.registry.setSelectionReady(false); // A failed request leaves this false.
    assert.throws(() => f.registry.assertCurrent(snapshot), /finish loading/);
    f.registry.setSelectionReady(true, false); // Metric-only redraw still has old entity data.
    assert.equal(f.registry.canExport(), false);
    f.registry.setSelectionReady(true);
    assert.throws(() => f.registry.assertCurrent(snapshot), /view changed/);
});

test('chart redraws and stale provenance cancel an export captured before asynchronous work', () => {
    const f = fixture(); f.registry.setSelectionReady(true);
    const snapshot = f.registry.snapshot('worldMap');
    f.registry.changed('worldMap');
    assert.throws(() => f.registry.assertCurrent(snapshot), /view changed/);
    f.browser.gwasProvenance.isCurrent = () => false;
    assert.equal(f.registry.canExport(), false);
});

test('CSV quotes Unicode and newlines and neutralises only formula-like text', () => {
    const text = {type: 'text'}, number = {type: 'number'};
    assert.equal(exportsApi.csvCell('Café, "test"\nnext', text), '"Café, ""test""\nnext"');
    for (const value of ['=1+1', '+CMD', '-12', '@SUM(A1)', '  =1', '\tHello', '\rHello', '\nHello']) {
        assert.ok(exportsApi.csvCell(value, text).replace(/^"/, '').startsWith("'"), value);
    }
    assert.equal(exportsApi.csvCell(-12, number), '-12');
    assert.equal(exportsApi.csvCell('12.25', number), '12.25');
    assert.equal(exportsApi.csvCell(null, number), '');
    assert.equal(exportsApi.csvCell(NaN, number), '');
    assert.equal(exportsApi.csvCell('plain', text), 'plain');
});

test('CSV yields between bounded chunks and stops immediately on state changes or byte limits', async () => {
    const f = fixture(); f.registry.setSelectionReady(true);
    const snapshot = {...f.registry.snapshot('bubbleGraph'), rowCount: 1001,
        rowAt: index => ({label: 'row' + index, count: index})};
    let yielded = 0;
    const file = await exportsApi.csvFile(snapshot, {guard: () => {}, yieldWork: async () => { yielded++; }});
    assert.equal(yielded, 3);
    assert.equal(file.parts.length, 4);
    assert.match(new TextDecoder().decode(file.parts[3]), /row1000,1000/);
    await assert.rejects(exportsApi.csvFile(snapshot, {limit: 10}), /limit/);
    await assert.rejects(exportsApi.csvFile(snapshot, {guard: () => { throw Error('changed'); }}), /changed/);
});

test('ZIP CRC and headers are standard and unsafe names are rejected', async () => {
    const file = exportsApi.textFile('test.txt', '123456789');
    assert.equal(file.crc, 0xcbf43926);
    const bytes = new Uint8Array(await exportsApi.zip([file]).arrayBuffer());
    assert.equal(new DataView(bytes.buffer).getUint32(0, true), 0x04034b50);
    assert.equal(new DataView(bytes.buffer).getUint16(6, true), 0x800);
    assert.throws(() => exportsApi.zip([{...file, name: '../unsafe'}]), /filename/);
    assert.throws(() => exportsApi.zip([file, file]), /filename/);
});

test('bundle keeps loaded provenance and contains chart-specific methodology', async () => {
    const f = fixture(); f.registry.setSelectionReady(true);
    const snapshot = f.registry.snapshot('doughnutGraph');
    const meta = exportsApi.metadata(snapshot);
    assert.equal(meta.provenance.datasetId, f.browser.gwasProvenance.loaded.datasetId);
    assert.equal(meta.chart.settings.year, 2020);
    const blob = await exportsApi.bundle([snapshot], {yieldWork: async () => {}});
    assert.equal(blob.type, 'application/zip');
    await assert.rejects(exportsApi.bundle([snapshot, {...snapshot, provenance: {datasetId: 'other'}}]), /different datasets/);
});
