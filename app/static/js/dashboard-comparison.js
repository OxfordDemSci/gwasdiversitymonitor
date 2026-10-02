(function() {
    'use strict';

    function initialiseComparison() {
        const dialog = document.getElementById('dashboard-comparison');
        const opener = document.getElementById('dashboard-compare-open');
        if (!dialog || !opener) return;

        const form = document.getElementById('comparison-form');
        const submit = document.getElementById('comparison-submit');
        const status = document.getElementById('comparison-status');
        const error = document.getElementById('comparison-error');
        const results = document.getElementById('comparison-results');
        const stage = document.getElementById('comparison-stage');
        const metric = document.getElementById('comparison-metric');
        const fromYear = document.getElementById('comparison-from-year');
        const toYear = document.getElementById('comparison-to-year');
        const facetSelects = Array.from(form.querySelectorAll('[data-comparison-facet]'));
        const numberFormat = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 0});
        const shareFormat = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 1});
        const facetRequests = new Set();
        let initialised = false;
        let revision = 0;
        let controller = null;

        function number(value) {
            return typeof value === 'number' && Number.isFinite(value)
                ? numberFormat.format(value) : '—';
        }

        function percentage(value) {
            return typeof value === 'number' && Number.isFinite(value)
                ? shareFormat.format(value) + '%' : '—';
        }

        function selected(side, facet) {
            const select = document.getElementById('comparison-' + side + '-' + facet);
            return Array.from(select.selectedOptions, option => option.value).filter(Boolean);
        }

        function setBusy(busy) {
            form.setAttribute('aria-busy', String(busy));
            submit.disabled = busy;
            submit.textContent = busy ? 'Comparing…' : 'Compare selections';
        }

        function clearError() {
            error.hidden = true;
            error.textContent = '';
            fromYear.setCustomValidity('');
        }

        function cancelPending() {
            revision += 1;
            if (controller) controller.abort();
            controller = null;
            facetRequests.forEach(request => request.abort());
            facetRequests.clear();
            setBusy(false);
        }

        function closeDropdowns() {
            if (!initialised) return;
            facetSelects.forEach(select => window.jQuery(select).select2('close'));
        }

        function invalidate() {
            if (window.gwasChartData) window.gwasChartData.invalidate('comparison');
            cancelPending();
            closeDropdowns();
            results.hidden = true;
            clearError();
            status.textContent = 'Settings changed. Compare selections to update the results.';
        }

        function initialiseFacet(select) {
            const $ = window.jQuery;
            const side = select.dataset.comparisonSide;
            const facet = select.dataset.comparisonFacet;
            const oppositeFacet = facet === 'funders' ? 'cohorts' : 'funders';
            $(select).select2({
                width: '100%',
                placeholder: facet === 'funders' ? 'All funders' : 'All cohorts',
                allowClear: true,
                maximumSelectionLength: 20,
                dropdownParent: $(dialog),
                ajax: {
                    url: '/api/' + facet,
                    dataType: 'json',
                    delay: 150,
                    data: function(params) {
                        const data = {
                            search: params.term || '',
                            stage: stage.value,
                            page: params.page || 1,
                            _comparisonRevision: revision
                        };
                        data[oppositeFacet] = selected(side, oppositeFacet).join(',');
                        return data;
                    },
                    transport: function(params, success, failure) {
                        const requestedRevision = params.data._comparisonRevision;
                        if (!dialog.open || requestedRevision !== revision) return {abort: function() {}};
                        const requestData = Object.assign({}, params.data);
                        delete requestData._comparisonRevision;
                        const request = $.ajax(Object.assign({}, params, {data: requestData}));
                        facetRequests.add(request);
                        request.done(function(data) {
                            if (requestedRevision === revision && dialog.open) success(data);
                        }).fail(function(xhr, requestStatus, reason) {
                            if (requestStatus !== 'abort' && requestedRevision === revision && dialog.open) {
                                failure(xhr, requestStatus, reason);
                            }
                        }).always(function() { facetRequests.delete(request); });
                        return request;
                    },
                    processResults: function(data) {
                        return {results: data.results || [], pagination: data.pagination || {more: false}};
                    }
                }
            });
            $(select).next('.select2-container')
                .find('.select2-selection, .select2-search__field')
                .attr('aria-labelledby', select.id + '-label')
                .attr('aria-describedby', 'comparison-selection-help');
            $(select).on('change.dashboardComparison', invalidate);
            $(select).on('select2:open.dashboardComparison', function() {
                facetSelects.filter(other => other !== select)
                    .forEach(other => $(other).select2('close'));
            });
        }

        function seedFacet(sourceId, destinationId) {
            const source = document.getElementById(sourceId);
            const destination = document.getElementById(destinationId);
            if (!source) return;
            Array.from(source.selectedOptions).forEach(option => {
                if (option.value) destination.add(new Option(option.textContent, option.value, true, true));
            });
        }

        function initialiseFields() {
            if (initialised) return;
            if (!window.jQuery || !window.jQuery.fn.select2) {
                throw new Error('The selection controls could not load. Reload the page and try again.');
            }
            seedFacet('funder-filter', 'comparison-left-funders');
            seedFacet('dataset-filter', 'comparison-left-cohorts');
            const dashboardStage = document.getElementById('cb2');
            stage.value = dashboardStage && dashboardStage.checked ? 'replication' : 'initial';
            facetSelects.forEach(initialiseFacet);
            initialised = true;
        }

        function showError(message) {
            status.textContent = '';
            error.textContent = message;
            error.hidden = false;
            submit.textContent = 'Retry comparison';
        }

        function appendCell(row, tag, text, scope) {
            const cell = document.createElement(tag);
            cell.textContent = text;
            if (scope) cell.scope = scope;
            row.appendChild(cell);
            return cell;
        }

        function appendSummary(body, label, left, right, format) {
            const row = document.createElement('tr');
            appendCell(row, 'th', label, 'row');
            appendCell(row, 'td', format(left));
            appendCell(row, 'td', format(right));
            body.appendChild(row);
        }

        function appendShare(row, ancestry, denominator, side) {
            appendCell(row, 'td', number(ancestry.count));
            const valid = denominator > 0 && typeof ancestry.percentage === 'number'
                && Number.isFinite(ancestry.percentage);
            const cell = appendCell(row, 'td', '', null);
            cell.className = 'dashboard-comparison__share';
            const text = document.createElement('span');
            text.textContent = valid ? percentage(ancestry.percentage) : '—';
            if (!valid) text.setAttribute('aria-label', 'No recorded data');
            cell.appendChild(text);
            const track = document.createElement('span');
            track.className = 'dashboard-comparison__bar-track';
            track.setAttribute('aria-hidden', 'true');
            const bar = document.createElement('span');
            bar.className = 'dashboard-comparison__bar dashboard-comparison__bar--' + side;
            bar.style.width = (valid ? Math.min(100, Math.max(0, ancestry.percentage)) : 0) + '%';
            track.appendChild(bar);
            cell.appendChild(track);
        }

        function render(data) {
            const left = data.left;
            const right = data.right;
            const settings = data.settings;
            document.getElementById('comparison-left-label').textContent = left.label;
            document.getElementById('comparison-right-label').textContent = right.label;
            const years = settings.fromYear || settings.toYear
                ? 'Publication years ' + (settings.fromYear || 'earliest') + '–' + (settings.toYear || 'latest')
                : 'All publication years';
            document.getElementById('comparison-result-settings').textContent =
                (settings.stage === 'replication' ? 'Replication' : 'Discovery') + ' · ' + years
                + ' · ' + (settings.metric === 'studies' ? 'Distinct study memberships' : 'Participant instances');

            const summary = document.getElementById('comparison-summary-rows');
            summary.replaceChildren();
            [
                ['Distinct studies', 'studyCount', number],
                ['Distinct publications', 'publicationCount', number],
                ['Participant instances', 'participantCount', number],
                ['Participant instances with recorded ancestry', 'recordedParticipantCount', number],
                ['Participant instances with recorded ancestry (%)', 'ancestryReportingPercentage', percentage],
                ['Studies with recorded ancestry (%)', 'recordedStudyPercentage', percentage],
                ['Studies with recorded cohorts', 'cohortReportingPercentage', percentage],
                ['Publications with recorded funding', 'fundingCoveragePercentage', percentage]
            ].forEach(([label, key, format]) => appendSummary(summary, label, left[key], right[key], format));
            appendSummary(summary, 'Ancestry share denominator', left.denominator, right.denominator, number);

            const body = document.getElementById('comparison-ancestry-rows');
            body.replaceChildren();
            left.ancestries.forEach((ancestry, index) => {
                const row = document.createElement('tr');
                appendCell(row, 'th', ancestry.name, 'row');
                appendShare(row, ancestry, left.denominator, 'left');
                appendShare(row, right.ancestries[index], right.denominator, 'right');
                body.appendChild(row);
            });
            if (!left.ancestries.length) {
                const row = document.createElement('tr');
                const cell = appendCell(row, 'td', 'No recorded ancestry data in either selection.');
                cell.colSpan = 5;
                body.appendChild(row);
            }
            const empty = document.getElementById('comparison-empty');
            const notices = [];
            [['A', left], ['B', right]].forEach(([label, side]) => {
                if (side.empty) notices.push('Selection ' + label + ' has no matching studies. Try widening its filters or publication years.');
                else if (!side.denominator) notices.push('Selection ' + label + ' has no recorded ancestry denominator. Its shares are unavailable.');
            });
            empty.textContent = notices.join(' ');
            empty.hidden = notices.length === 0;
            document.getElementById('comparison-denominator').textContent =
                'Share denominator: ' + data.methodology.denominator + '. Count unit: ' + data.methodology.unit + '.';
            document.getElementById('comparison-methodology').textContent =
                data.methodology.note + (data.methodology.dateBasis ? ' ' + data.methodology.dateBasis : '');
            document.getElementById('comparison-overlap').textContent =
                'Shared between A and B: ' + number(data.overlap.studyCount) + ' distinct studies and '
                + number(data.overlap.publicationCount) + ' distinct publications.';
            results.hidden = false;
            if (window.gwasChartData) {
                const captured = data;
                window.gwasChartData.register('comparison', function() {
                    const sides = ['left', 'right'];
                    const count = captured.left.ancestries.length;
                    return {
                        id: 'comparison', title: 'Side-by-side comparison',
                        columns: [
                            {key: 'side', label: 'Side', type: 'text'},
                            {key: 'selection', label: 'Selection', type: 'text'},
                            {key: 'ancestry', label: 'Ancestry', type: 'text'},
                            {key: 'count', label: 'Count', type: 'number'},
                            {key: 'percentage', label: 'Share (%)', type: 'number'},
                            {key: 'denominator', label: 'Recorded-ancestry denominator', type: 'number'}
                        ],
                        rowCount: count * 2,
                        rowAt: function(index) {
                            const side = captured[sides[Math.floor(index / count)]];
                            const entry = side.ancestries[index % count];
                            return {side: index < count ? 'A' : 'B', selection: side.label, ancestry: entry.name, count: entry.count,
                                percentage: entry.percentage, denominator: side.denominator};
                        },
                        settings: {comparison: captured.settings, overlap: captured.overlap,
                            left: Object.assign({}, captured.left, {ancestries: undefined}),
                            right: Object.assign({}, captured.right, {ancestries: undefined})},
                        methodology: [captured.methodology.denominator, captured.methodology.note,
                            captured.methodology.dateBasis,
                            'Selections use full counting and can overlap. Participant instances are not unique people; unique-person overlap cannot be estimated.']
                    };
                });
            }
        }

        opener.addEventListener('click', function() {
            if (dialog.open) return;
            dialog.showModal();
            clearError();
            try {
                initialiseFields();
            } catch (failure) {
                showError(failure.message);
            }
        });
        document.getElementById('comparison-close').addEventListener('click', function() { dialog.close(); });
        dialog.addEventListener('cancel', function() { cancelPending(); closeDropdowns(); });
        dialog.addEventListener('close', function() {
            cancelPending();
            closeDropdowns();
            status.textContent = '';
            opener.focus();
        });
        [stage, metric].forEach(input => input.addEventListener('change', invalidate));
        [fromYear, toYear].forEach(input => input.addEventListener('input', invalidate));

        form.addEventListener('submit', async function(event) {
            event.preventDefault();
            if (window.gwasChartData) window.gwasChartData.invalidate('comparison');
            cancelPending();
            closeDropdowns();
            clearError();
            results.hidden = true;
            if (window.gwasProvenance && !window.gwasProvenance.isCurrent()) {
                showError(window.gwasProvenance.message);
                return;
            }
            try {
                initialiseFields();
            } catch (failure) {
                showError(failure.message);
                return;
            }
            if (fromYear.value && toYear.value && Number(fromYear.value) > Number(toYear.value)) {
                fromYear.setCustomValidity('The start year must be before or equal to the end year.');
            }
            if (!form.reportValidity()) return;
            const payload = {
                datasetId: window.gwasProvenance ? window.gwasProvenance.loaded.datasetId || null : null,
                left: {funders: selected('left', 'funders'), cohorts: selected('left', 'cohorts')},
                right: {funders: selected('right', 'funders'), cohorts: selected('right', 'cohorts')},
                stage: stage.value,
                metric: metric.value,
                fromYear: fromYear.value ? Number(fromYear.value) : null,
                toYear: toYear.value ? Number(toYear.value) : null
            };
            const requestedRevision = revision;
            controller = new AbortController();
            setBusy(true);
            status.textContent = 'Calculating the comparison…';
            try {
                const response = await fetch('/api/comparison', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                    body: JSON.stringify(payload),
                    signal: controller.signal,
                    credentials: 'same-origin'
                });
                if (response.status === 409 && window.gwasProvenance) {
                    window.gwasProvenance.markStale();
                    throw new Error(window.gwasProvenance.message);
                }
                const data = await response.json();
                if (requestedRevision !== revision || !dialog.open) return;
                if (!response.ok) {
                    throw new Error(typeof data.error === 'string' ? data.error : 'The comparison could not be loaded. Please try again.');
                }
                if (window.gwasProvenance && !window.gwasProvenance.isCurrent(data.datasetId)) {
                    window.gwasProvenance.markStale();
                    throw new Error(window.gwasProvenance.message);
                }
                render(data);
                status.textContent = 'Comparison ready. Counts, coverage and ancestry shares are shown below.';
            } catch (failure) {
                if (failure.name === 'AbortError' || requestedRevision !== revision || !dialog.open) return;
                showError(failure instanceof SyntaxError || failure instanceof TypeError
                    ? 'The comparison could not be loaded. Check your connection and retry.' : failure.message);
            } finally {
                if (requestedRevision === revision) {
                    controller = null;
                    setBusy(false);
                    if (!error.hidden) submit.textContent = 'Retry comparison';
                }
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialiseComparison, {once: true});
    } else {
        initialiseComparison();
    }
})();
