/* Keep complete facet catalogues searchable without rendering every row at once. */
(function(root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.GwasFilterOptions = api;
})(typeof window === 'undefined' ? globalThis : window, function() {
    'use strict';

    function normalize(value) {
        let text = String(value || '');
        if (text.normalize) text = text.normalize('NFKC');
        return text.replace(/\s+/g, ' ').trim().toLocaleLowerCase();
    }

    function complete(payload) {
        return !!payload && Array.isArray(payload.results) &&
            !(payload.pagination && payload.pagination.more);
    }

    function create(pageSize) {
        const size = pageSize || 50;
        // Weak keys let indexes disappear when their versioned catalogue is
        // evicted. Never mutate rows: Select2 adds its own selection metadata.
        const indexes = new WeakMap();
        function matching(payload, search) {
            const needle = normalize(search);
            if (!needle) return payload.results;
            let index = indexes.get(payload);
            if (!index) {
                index = {rows: payload.results.map(entry => ({
                    entry: entry, text: normalize(entry.text), id: normalize(entry.id)
                })), searches: new Map()};
                indexes.set(payload, index);
            }
            if (index.searches.has(needle)) {
                const rows = index.searches.get(needle);
                index.searches.delete(needle);
                index.searches.set(needle, rows);
                return rows;
            }
            const rows = index.rows.filter(row =>
                row.text.includes(needle) || row.id.includes(needle)
            ).map(row => row.entry);
            index.searches.set(needle, rows);
            if (index.searches.size > 8) index.searches.delete(index.searches.keys().next().value);
            return rows;
        }

        return {
            page: function(payload, page, search) {
                if (!complete(payload)) return payload;
                const rows = matching(payload, search);
                const number = Number(page);
                const start = (Number.isFinite(number) ? Math.max(1, Math.floor(number)) - 1 : 0) * size;
                const result = Object.assign({}, payload, {
                    results: rows.slice(start, start + size).map(entry => Object.assign({}, entry)),
                    pagination: Object.assign({}, payload.pagination, {more: start + size < rows.length})
                });
                // Internal sizing metadata; not an API field or extra option.
                // One hidden sizing row preserves the full list's auto width.
                if (rows.length > size) Object.defineProperty(result, '_gwasFacetRows', {value: rows});
                return result;
            }
        };
    }

    return {create: create, complete: complete, normalize: normalize};
});
