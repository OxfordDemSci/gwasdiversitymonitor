/* Paginated views of the same lazy chart snapshots used by publication exports. */
(function(root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        const start = () => { root.gwasChartTable = api.create(root); };
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, {once: true});
        else start();
    }
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    const PAGE_SIZE = 50;
    function pageRows(snapshot, page) {
        const pages = Math.max(1, Math.ceil(snapshot.rowCount / PAGE_SIZE));
        const current = Math.min(Math.max(Number.isInteger(page) ? page : 0, 0), pages - 1);
        const start = current * PAGE_SIZE, end = Math.min(start + PAGE_SIZE, snapshot.rowCount);
        const rows = [];
        for (let index = start; index < end; index++) rows.push(snapshot.rowAt(index));
        return {page: current, pages: pages, start: start, end: end, rows: rows};
    }
    function create(browser) {
        const document = browser.document, registry = browser.gwasChartData;
        const node = name => document.getElementById('dashboard-table-' + name);
        const dialog = node('dialog');
        if (!dialog || !registry) return null;
        let snapshot = null, chartId = null, page = 0, trigger = null, busy = false;
        function element(tag, text) {
            const el = document.createElement(tag);
            if (text !== undefined) el.textContent = text;
            return el;
        }
        function updateButtons() {
            const ready = registry.canExport();
            node('refresh').disabled = !ready || !chartId;
            node('export').disabled = !ready || !snapshot || busy;
            document.querySelectorAll('[data-chart-table]').forEach(button => {
                button.disabled = !ready;
                button.setAttribute('aria-disabled', String(!ready));
            });
        }
        function invalidate(message) {
            snapshot = null;
            node('body').replaceChildren();
            node('range').textContent = '';
            node('previous').disabled = true;
            node('next').disabled = true;
            node('status').textContent = message || 'The view changed. Refresh this table after the charts finish loading.';
            updateButtons();
        }
        function renderPage(nextPage) {
            try {
                registry.assertCurrent(snapshot);
                const result = pageRows(snapshot, nextPage);
                page = result.page;
                const fragment = document.createDocumentFragment();
                result.rows.forEach(row => {
                    const tr = element('tr');
                    snapshot.columns.forEach(column => {
                        const value = row[column.key];
                        const td = element('td', value === null || value === undefined ? '' : String(value));
                        if (column.type === 'number') td.className = 'dashboard-table__number';
                        tr.appendChild(td);
                    });
                    fragment.appendChild(tr);
                });
                node('body').replaceChildren(fragment);
                node('range').textContent = snapshot.rowCount
                    ? 'Rows ' + (result.start + 1).toLocaleString('en-GB') + '–' + result.end.toLocaleString('en-GB')
                        + ' of ' + snapshot.rowCount.toLocaleString('en-GB')
                    : 'No rows match this chart view.';
                node('previous').disabled = page === 0;
                node('next').disabled = page + 1 >= result.pages;
                node('status').textContent = '';
                updateButtons();
                return true;
            } catch (error) {
                invalidate('This table is no longer current. Refresh it after the selected view loads successfully.');
                return false;
            }
        }
        function refresh() {
            try {
                snapshot = registry.snapshot(chartId);
                node('title').textContent = snapshot.title + ' — data';
                node('caption').textContent = snapshot.title + ': exact plotted values';
                const settings = Object.keys(snapshot.settings).map(key => {
                    const value = snapshot.settings[key];
                    return key.replace(/([A-Z])/g, ' $1').toLowerCase() + ': '
                        + (typeof value === 'object' ? JSON.stringify(value) : String(value));
                });
                node('context').textContent = 'Dataset: ' + snapshot.provenance.datasetId + '. ' + settings.join('; ');
                const notes = snapshot.methodology.map(text => element('li', text));
                node('methodology').replaceChildren(...notes);
                const tr = element('tr');
                snapshot.columns.forEach(column => {
                    const th = element('th', column.label);
                    th.setAttribute('scope', 'col');
                    tr.appendChild(th);
                });
                node('head').replaceChildren(tr);
                return renderPage(0);
            } catch (error) {
                invalidate('Wait for this view to finish loading successfully, then refresh the table.');
                return false;
            }
        }
        function open(id, source) {
            if (!registry.canExport()) {
                registry.reportError('Wait for the selected view to finish loading successfully before opening its data table.');
                return false;
            }
            chartId = id;
            trigger = source || document.activeElement;
            if (!refresh()) return false;
            if (!dialog.open) dialog.showModal();
            node('close').focus();
            return true;
        }
        function close() { dialog.close(); }
        node('close').addEventListener('click', close);
        node('refresh').addEventListener('click', refresh);
        node('previous').addEventListener('click', () => { if (snapshot) renderPage(page - 1); });
        node('next').addEventListener('click', () => { if (snapshot) renderPage(page + 1); });
        dialog.addEventListener('close', () => {
            snapshot = null;
            node('body').replaceChildren();
            if (trigger && trigger.isConnected && typeof trigger.focus === 'function') trigger.focus();
        });
        node('export').addEventListener('click', async () => {
            if (!snapshot || busy) return;
            const captured = snapshot;
            busy = true;
            updateButtons();
            try {
                registry.assertCurrent(captured);
                node('status').textContent = 'Preparing the table’s chart data bundle…';
                const exporter = await registry.loadExporter();
                registry.assertCurrent(captured);
                await exporter.downloadSnapshots([captured]);
                if (snapshot === captured) node('status').textContent = 'Data bundle downloaded with view settings, provenance and citations.';
            } catch (error) { node('status').textContent = error.message; }
            finally { busy = false; updateButtons(); }
        });
        document.addEventListener('click', event => {
            const button = event.target.closest('[data-chart-table]');
            if (!button) return;
            event.preventDefault();
            open(button.getAttribute('data-chart-table'), button);
        });
        ['gwas:chartdatachanged', 'gwas:viewchange', 'gwas:datasetstale'].forEach(name => browser.addEventListener(name, () => {
            if (dialog.open && snapshot) {
                try { registry.assertCurrent(snapshot); }
                catch (error) { invalidate(); }
            }
            updateButtons();
        }));
        updateButtons();
        return {open: open, close: close, refresh: refresh};
    }
    return {create: create, pageRows: pageRows, PAGE_SIZE: PAGE_SIZE};
});
