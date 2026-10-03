/* One verified path for eager responses, retries, and browsers without fetch. */
(function(root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.GwasDashboardLoading = api;
})(typeof window !== 'undefined' ? window : this, function() {
    'use strict';
    function create(browser, bootstrap) {
        const pending = new Map(), values = new Map();
        function verify(status, identifier) {
            const expected = browser.gwasLoadedProvenance && browser.gwasLoadedProvenance.datasetId;
            if (status === 409 || (status >= 200 && status < 300 && expected && identifier !== expected)) {
                if (browser.gwasProvenance) browser.gwasProvenance.markStale();
                const error = new Error('The dataset changed. Reload this view.');
                error.status = 409;
                throw error;
            }
            if (status < 200 || status >= 300) {
                const error = new Error('The data request failed (HTTP ' + status + ').');
                error.status = status;
                throw error;
            }
            if (browser.gwasProvenance && !browser.gwasProvenance.isCurrent()) {
                const error = new Error('Reload to load one consistent dataset.');
                error.status = 409;
                throw error;
            }
        }
        function fetchResponse(url) {
            if (browser.fetch) return browser.fetch(url, {credentials: 'same-origin'}).then(response => ({response}));
            return new Promise((resolve, reject) => {
                const xhr = new browser.XMLHttpRequest();
                xhr.open('GET', url, true);
                xhr.onload = function() {
                    resolve({response: {status: xhr.status,
                        headers: {get: name => xhr.getResponseHeader(name)},
                        json: () => Promise.resolve().then(() => JSON.parse(xhr.responseText))}});
                };
                xhr.onerror = () => reject(new Error('Check your connection and retry.'));
                xhr.send();
            });
        }
        function load(name, retry) {
            if (browser.gwasProvenance && !browser.gwasProvenance.isCurrent()) {
                return Promise.reject(Object.assign(new Error('Reload to load one consistent dataset.'), {status: 409}));
            }
            if (values.has(name)) return Promise.resolve(values.get(name));
            if (pending.has(name)) return pending.get(name);
            const early = !retry && bootstrap.requests[name];
            delete bootstrap.requests[name];
            const request = Promise.resolve(early || fetchResponse(bootstrap.urls[name])).then(result => {
                if (result.error) throw result.error;
                const response = result.response;
                verify(response.status, response.headers.get('X-GWAS-Dataset-ID'));
                return response.json();
            }).then(data => {
                if (browser.gwasProvenance && !browser.gwasProvenance.isCurrent()) verify(409, null);
                values.set(name, data);
                return data;
            }).finally(() => pending.delete(name));
            pending.set(name, request);
            return request;
        }
        return {load, verify};
    }
    return {create};
});
