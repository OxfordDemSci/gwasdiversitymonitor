/* Loaded only when a data or image export is requested. ZIP uses stored entries. */
(function(root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.gwasExports = Object.assign({}, api, {downloadSnapshots: api.forBrowser(root)});
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    const MAX_BYTES = 128 * 1024 * 1024;
    const CITATION = 'Mills, M. C. & Rahal, C. (2020). The GWAS Diversity Monitor tracks diversity by disease in real time. Nature Genetics 52, 242–243. https://doi.org/10.1038/s41588-020-0580-y';
    const SOFTWARE_CITATION = 'Boef, N., Brunier, Q., Knowles, I., Malowany, A., May, J., Mills, M. C., Misseri, L., Nixon, G., Ntova, V., Rahal, C. & Sinclair, C. (2020). Source code for the GWAS Diversity Monitor (Version 1.0.0). Zenodo. https://doi.org/10.5281/zenodo.3600472';
    const encoder = new TextEncoder();
    const crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
        let value = n;
        for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
        crcTable[n] = value >>> 0;
    }
    function crcUpdate(crc, bytes) {
        for (let index = 0; index < bytes.length; index++) crc = crcTable[(crc ^ bytes[index]) & 255] ^ (crc >>> 8);
        return crc >>> 0;
    }
    function csvCell(value, column) {
        if (value === undefined || value === null || value === '') return '';
        if (column && column.type === 'number') {
            const number = Number(value);
            return Number.isFinite(number) ? String(number) : '';
        }
        let text = String(value);
        // Spreadsheet formula neutralisation applies only to text columns.
        if (/^[\t\r\n]/.test(text) || /^\s*[=+\-@]/.test(text)) text = "'" + text;
        return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }
    function textFile(name, text) {
        const bytes = encoder.encode(text);
        return {name: name, parts: [bytes], size: bytes.length, crc: (crcUpdate(0xffffffff, bytes) ^ 0xffffffff) >>> 0};
    }
    async function csvFile(snapshot, options) {
        options = options || {};
        const guard = options.guard || function() {}, progress = options.progress || function() {};
        const yieldWork = options.yieldWork || (() => new Promise(resolve => setTimeout(resolve, 0)));
        const limit = options.limit || MAX_BYTES;
        const parts = [];
        let size = 0, crc = 0xffffffff;
        function append(text) {
            const bytes = encoder.encode(text);
            size += bytes.length;
            if (size > limit) throw new Error('This export exceeds the 128 MiB limit. Narrow the selection and try again.');
            crc = crcUpdate(crc, bytes);
            parts.push(bytes);
        }
        append(snapshot.columns.map(column => csvCell(column.label)).join(',') + '\r\n');
        for (let start = 0; start < snapshot.rowCount; start += 500) {
            guard(snapshot);
            const end = Math.min(snapshot.rowCount, start + 500), lines = [];
            for (let index = start; index < end; index++) {
                const row = snapshot.rowAt(index);
                lines.push(snapshot.columns.map(column => csvCell(row[column.key], column)).join(','));
            }
            append(lines.join('\r\n') + '\r\n');
            progress('Preparing ' + snapshot.title + ': ' + end.toLocaleString('en-GB') + ' of ' + snapshot.rowCount.toLocaleString('en-GB') + ' rows…');
            await yieldWork();
        }
        guard(snapshot);
        return {name: snapshot.id + '.csv', parts: parts, size: size, crc: (crc ^ 0xffffffff) >>> 0};
    }
    function zip(files) {
        if (!files.length || files.length > 100) throw new Error('Invalid number of export files.');
        const localParts = [], directoryParts = [], names = new Set();
        let offset = 0, directorySize = 0;
        files.forEach(file => {
            if (!/^[A-Za-z0-9_.-]{1,150}$/.test(file.name) || names.has(file.name)) throw new Error('Invalid export filename.');
            names.add(file.name);
            const name = encoder.encode(file.name);
            const size = file.parts.reduce((total, part) => total + part.byteLength, 0);
            if (size !== file.size || offset + size > MAX_BYTES) throw new Error('This export exceeds the 128 MiB limit. Narrow the selection and try again.');
            const local = new Uint8Array(30 + name.length), view = new DataView(local.buffer);
            view.setUint32(0, 0x04034b50, true); view.setUint16(4, 20, true);
            view.setUint16(6, 0x800, true); view.setUint16(12, 33, true);
            view.setUint32(14, file.crc, true); view.setUint32(18, size, true); view.setUint32(22, size, true);
            view.setUint16(26, name.length, true); local.set(name, 30);
            localParts.push(local, ...file.parts);
            const central = new Uint8Array(46 + name.length), entry = new DataView(central.buffer);
            entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true);
            entry.setUint16(8, 0x800, true); entry.setUint16(14, 33, true);
            entry.setUint32(16, file.crc, true); entry.setUint32(20, size, true); entry.setUint32(24, size, true);
            entry.setUint16(28, name.length, true); entry.setUint32(42, offset, true); central.set(name, 46);
            directoryParts.push(central); directorySize += central.length; offset += local.length + size;
        });
        const end = new Uint8Array(22), view = new DataView(end.buffer);
        view.setUint32(0, 0x06054b50, true); view.setUint16(8, files.length, true); view.setUint16(10, files.length, true);
        view.setUint32(12, directorySize, true); view.setUint32(16, offset, true);
        if (offset + directorySize + end.length > MAX_BYTES) throw new Error('This export exceeds the 128 MiB limit. Narrow the selection and try again.');
        return new Blob([...localParts, ...directoryParts, end], {type: 'application/zip'});
    }
    function metadata(snapshot) {
        return {
            schemaVersion: 1, exportedAt: snapshot.exportedAt, shareUrl: snapshot.shareUrl,
            provenance: snapshot.provenance, view: snapshot.view,
            chart: {id: snapshot.id, title: snapshot.title, columns: snapshot.columns,
                rowCount: snapshot.rowCount, settings: snapshot.settings, methodology: snapshot.methodology},
            citation: CITATION, softwareCitation: SOFTWARE_CITATION,
            csvTextSafety: 'Text cells beginning with spreadsheet formula characters are prefixed with an apostrophe; numeric columns remain numeric.'
        };
    }
    async function bundle(snapshots, options) {
        options = options || {};
        if (!snapshots.length) throw new Error('Choose at least one chart to export.');
        const guard = options.guard || function() {};
        snapshots.forEach(guard);
        if (snapshots.some(snapshot => snapshot.provenance.datasetId !== snapshots[0].provenance.datasetId)) throw new Error('Charts from different datasets cannot be combined.');
        const files = [];
        let size = 0;
        for (const snapshot of snapshots) {
            const file = await csvFile(snapshot, options);
            size += file.size;
            if (size > MAX_BYTES) throw new Error('This export exceeds the 128 MiB limit. Narrow the selection and try again.');
            files.push(file);
        }
        const first = metadata(snapshots[0]);
        const view = {schemaVersion: 1, exportedAt: first.exportedAt, shareUrl: first.shareUrl, view: first.view,
            charts: snapshots.map(snapshot => metadata(snapshot).chart)};
        files.push(textFile('view.json', JSON.stringify(view, null, 2) + '\n'));
        files.push(textFile('provenance.json', JSON.stringify(first.provenance, null, 2) + '\n'));
        files.push(textFile('CITATION.txt', CITATION + '\n\n' + SOFTWARE_CITATION + '\n'));
        files.push(textFile('README.txt', [
            'GWAS Diversity Monitor — displayed chart data',
            'Dataset: ' + first.provenance.datasetId,
            'Exported: ' + first.exportedAt, 'View: ' + first.shareUrl, '',
            'Each CSV contains the values displayed by its chart at export time. Chart-specific years and filters differ; see view.json.',
            'Historical values describe publication dates in this Catalog snapshot, not frozen historical Catalog releases.',
            'Participant instances are not unique people. Entity attribution uses full counting and totals may overlap.',
            first.csvTextSafety, '',
            ...snapshots.flatMap(snapshot => [snapshot.title + ' (' + snapshot.rowCount + ' rows)', ...snapshot.methodology, '']),
            options.image && snapshots[0].id === 'bubbleGraph'
                ? 'The bubble SVG embeds raster canvas artwork; it is not an entirely vector figure.' : '',
            'This bundle is separate from the entity-selection source ZIP, which includes all years, traits and both stages.',
        ].filter(value => value !== null).join('\n') + '\n'));
        if (options.image) {
            snapshots.forEach(guard);
            const bytes = new Uint8Array(await options.image.blob.arrayBuffer());
            files.push({name: options.image.name, parts: [bytes], size: bytes.length,
                crc: (crcUpdate(0xffffffff, bytes) ^ 0xffffffff) >>> 0});
        }
        snapshots.forEach(guard);
        return zip(files);
    }
    function forBrowser(browser) {
        let busy = false;
        return async function(snapshots, options) {
            if (busy) throw new Error('Another export is being prepared. Please wait for it to finish.');
            busy = true;
            try {
                const registry = browser.gwasChartData;
                const blob = await bundle(snapshots, Object.assign({}, options, {
                    guard: registry.assertCurrent, progress: registry.reportError
                }));
                snapshots.forEach(registry.assertCurrent);
                const url = browser.URL.createObjectURL(blob), link = browser.document.createElement('a');
                link.href = url;
                link.download = 'gwas-' + (snapshots.length === 1 ? snapshots[0].id : 'dashboard')
                    + (options && options.image ? '-figure.zip' : '-data.zip');
                browser.document.body.appendChild(link); link.click(); link.remove();
                browser.setTimeout(() => browser.URL.revokeObjectURL(url), 1000);
                registry.reportError('Export ready: chart data, view settings, dataset provenance and citations are included.');
                return true;
            } finally { busy = false; }
        };
    }
    return {MAX_BYTES: MAX_BYTES, CITATION: CITATION, SOFTWARE_CITATION: SOFTWARE_CITATION,
        csvCell: csvCell, csvFile: csvFile, textFile: textFile, zip: zip, metadata: metadata, bundle: bundle, forBrowser: forBrowser};
});
