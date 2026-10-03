'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const options = require('../../app/static/js/dashboard-filter-options.js');

function catalogue(count = 1367) {
    return {results: Array.from({length: count}, (_, index) => ({
        id: 'cohort-' + index, text: 'Cohort ' + index,
        studyCount: count - index, publicationCount: index + 1
    })), pagination: {more: false}, datasetId: 'fixture-release'};
}

test('complete catalogues render in bounded pages without losing, reordering or duplicating rows', () => {
    const source = catalogue(), pages = options.create(50), rows = [];
    let result, page = 1;
    do {
        result = pages.page(source, page++);
        assert.ok(result.results.length <= 50);
        assert.equal(result.datasetId, source.datasetId);
        rows.push(...result.results);
    } while (result.pagination.more);
    assert.deepEqual(rows, source.results);
    assert.equal(result.results.length, 17);
    assert.equal(source.pagination.more, false);
    assert.equal(source.results.length, 1367);
});

test('search covers the full catalogue and pages the matching results, not the unfiltered rows', () => {
    const source = catalogue(), pages = options.create(50);
    assert.deepEqual(pages.page(source, 1, 'Cohort 1300').results, [source.results[1300]]);
    const matches = source.results.filter(entry => entry.text.toLowerCase().includes('cohort 1'));
    assert.deepEqual(pages.page(source, 1, 'cohort 1').results, matches.slice(0, 50));
    assert.deepEqual(pages.page(source, 2, 'cohort 1').results, matches.slice(50, 100));
    assert.equal(pages.page(source, 1, 'not a cohort').pagination.more, false);
    assert.deepEqual(pages.page(source, 1, 'not a cohort').results, []);
});

test('cached search preserves existing Unicode, case, whitespace and ID matching', () => {
    const source = {results: [
        {id: 'id-only', text: 'Ａlpha   study'}, {id: 'beta', text: 'Βήτα'},
    ], pagination: {more: false}};
    const pages = options.create(50);
    assert.deepEqual(pages.page(source, 1, '  ALPHA study ').results, [source.results[0]]);
    assert.deepEqual(pages.page(source, 1, 'ID-ONLY').results, [source.results[0]]);
    assert.deepEqual(pages.page(source, 1, 'βήτα').results, [source.results[1]]);
});

test('Select2 metadata cannot poison cached counts or subsequent pages', () => {
    const source = catalogue(), pages = options.create(50);
    const first = pages.page(source, 1);
    first.results[0].selected = true;
    first.results[0].studyCount = -1;
    first.pagination.more = false;
    assert.equal(pages.page(source, 1).results[0].studyCount, 1367);
    assert.equal(pages.page(source, 1).results[0].selected, undefined);
    assert.equal(pages.page(source, 1).pagination.more, true);
});

test('normalised search index is reused, but new scoped or dataset payloads get distinct indexes', () => {
    const pages = options.create(50);
    let reads = 0;
    const source = {results: [{id: 'first', get text() { reads++; return 'Alpha'; }}]};
    pages.page(source, 1, 'missing');
    const builtReads = reads;
    pages.page(source, 1, 'still missing');
    assert.equal(reads, builtReads);
    const otherScope = {results: [{id: 'first', text: 'Beta', studyCount: 3}]};
    assert.deepEqual(pages.page(otherScope, 1, 'alpha').results, []);
    assert.equal(pages.page(otherScope, 1, 'beta').results[0].studyCount, 3);
    for (let i = 0; i < 20; i++) pages.page(source, 1, 'missing ' + i);
    assert.equal(pages.page(source, 1, 'alpha').results[0].text, 'Alpha');
});

test('incomplete server pages are passed through without treating a partial catalogue as complete', () => {
    const source = catalogue(50);
    source.pagination.more = true;
    assert.equal(options.complete(source), false);
    assert.equal(options.create(50).page(source, 2, 'anything'), source);
});

test('small and empty lists keep their content and have no load-more row', () => {
    const pages = options.create(50);
    for (const count of [0, 1, 49, 50]) {
        const source = catalogue(count);
        assert.deepEqual(pages.page(source, 1), source);
    }
    assert.equal(options.complete(null), false);
    assert.equal(options.complete({}), false);
});

test('out-of-range pages return no duplicates, and invalid pages start at page one', () => {
    const source = catalogue(51), pages = options.create(50);
    assert.deepEqual(pages.page(source, 3).results, []);
    assert.equal(pages.page(source, 3).pagination.more, false);
    for (const page of [undefined, 0, -1, Infinity, 'nonsense']) {
        assert.deepEqual(pages.page(source, page).results, source.results.slice(0, 50));
    }
});
