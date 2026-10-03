"""Large catalogue rendering and request coalescing, using test-only API replies."""
import json
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect


READY = 'window.gwasChartData && window.gwasChartData.canExport()'
ROWS = '#select2-dataset-filter-results .select2-results__option[aria-selected]'


def run(browser, base, artifacts):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    requests, errors, external, checks, timings = [], [], [], [], []
    held = {}
    hold_next = False
    dataset_id = None
    page.on('pageerror', lambda error: errors.append(str(error)))

    def catalogue(query):
        funders = query.get('funders', [''])[0]
        stage = query.get('stage', ['initial'])[0]
        partial = ',' in funders
        prefix = (funders or 'baseline') + ' ' + stage
        total = 123 if partial else 1367
        entries = [{'id': 'alpha' if i == total - 1 else 'fixture-option-%04d' % i,
                    'text': '%s cohort %04d' % (prefix, i),
                    'studyCount': i % 71 + 1, 'publicationCount': i % 36 + 1}
                   for i in range(total)]
        if total > 1000:
            entries[1000]['text'] += ' with an unusually long cohort description beyond the first page'
        search = query.get('search', [''])[0].casefold()
        if search:
            entries = [entry for entry in entries if search in entry['text'].casefold() or search in entry['id']]
        if partial:
            start = (int(query.get('page', ['1'])[0]) - 1) * 50
            return {'results': entries[start:start + 50], 'pagination': {'more': start + 50 < len(entries)}}
        return {'results': entries, 'pagination': {'more': False}}

    def reply(route, payload, status=200):
        route.fulfill(status=status, content_type='application/json',
                      headers={'X-GWAS-Dataset-ID': dataset_id}, body=json.dumps(payload))

    def intercept(route):
        nonlocal hold_next
        url = urlsplit(route.request.url)
        if url.netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
        elif url.path == '/api/cohorts':
            query = parse_qs(url.query)
            requests.append(query)
            payload = catalogue(query)
            if hold_next:
                hold_next = False
                held.update(route=route, payload=payload)
            else:
                reply(route, payload)
        else:
            route.continue_()

    context.route('http*://**/*', intercept)

    def load(funders=None):
        nonlocal dataset_id
        page.goto(base + '/', wait_until='domcontentloaded')
        page.wait_for_function(READY, timeout=30000)
        page.evaluate('document.fonts.ready')
        dataset_id = page.evaluate('gwasLoadedProvenance.datasetId')
        initial = page.evaluate('gwasDashboardState.capture()')
        if funders:
            assert page.evaluate('(state) => gwasDashboardState.restore(state)', dict(initial, funders=funders))
            page.wait_for_function(READY, timeout=30000)
        return initial

    def open_filter():
        page.locator('#dataset-filter').locator('xpath=following-sibling::span').locator('.select2-search__field').click()

    def search(term):
        page.locator('.select2-container--open .select2-search__field').fill(term)

    def wait_held():
        deadline = time.monotonic() + 15
        while 'route' not in held and time.monotonic() < deadline:
            page.wait_for_timeout(20)
        assert 'route' in held, 'The intended catalogue request was not intercepted'

    def release(status=200):
        reply(held['route'], held['payload'], status)
        held.clear()

    def scroll_all(total):
        while page.locator(ROWS).count() < total:
            before = page.locator(ROWS).count()
            page.locator('#select2-dataset-filter-results').evaluate('(node) => { node.scrollTop = node.scrollHeight; }')
            page.wait_for_function('(before) => document.querySelectorAll(%s).length > before' % json.dumps(ROWS), arg=before)
        assert page.locator(ROWS).count() == total
        assert page.locator('.select2-results__option--load-more').count() == 0

    def measure_open(mode, label):
        page.mouse.move(0, 0)
        result = page.evaluate("""() => new Promise(resolve => {
            const start = performance.now();
            $('#dataset-filter').select2('open');
            function painted() {
                const rows = document.querySelectorAll('#select2-dataset-filter-results [aria-selected]');
                if (!rows.length) return requestAnimationFrame(painted);
                requestAnimationFrame(() => {
                    const dropdown = document.querySelector('.dashboard-filter-dropdown');
                    const first = rows[0], style = getComputedStyle(first);
                    resolve({milliseconds: performance.now() - start, rows: rows.length,
                        appearance: {dropdownWidth: dropdown.getBoundingClientRect().width,
                            firstWidth: first.getBoundingClientRect().width, firstHeight: first.getBoundingClientRect().height,
                            color: style.color, background: style.backgroundColor, fontSize: style.fontSize,
                            padding: style.padding, countFont: getComputedStyle(first.querySelector('small')).fontSize}});
                });
            }
            requestAnimationFrame(painted);
        })""")
        # Geometry is checked after the existing 250ms highlight transition;
        # the measured opening duration above does not include this settling wait.
        page.wait_for_timeout(300)
        result['appearance'] = page.locator('.select2-container--open .dashboard-filter-dropdown:visible').evaluate("""dropdown => {
            const first = dropdown.querySelector('[aria-selected]'), style = getComputedStyle(first), d = getComputedStyle(dropdown);
            return {dropdownWidth: dropdown.getBoundingClientRect().width, minWidth:d.minWidth, maxWidth:d.maxWidth,
                firstWidth:first.getBoundingClientRect().width, firstHeight:first.getBoundingClientRect().height,
                color:style.color, background:style.backgroundColor, fontSize:style.fontSize,
                padding:style.padding, countFont:getComputedStyle(first.querySelector('small')).fontSize};
        }""")
        assert page.locator('.gwas-facet-width').evaluate_all("""nodes => nodes.every(node =>
            node.getBoundingClientRect().height === 0 && node.offsetHeight === 0 &&
            getComputedStyle(node).overflow === 'hidden' && node.getAttribute('aria-hidden') === 'true')""")
        if label == 'cold-open' or mode == 'unpaged-rendering-control-mobile390':
            page.locator('.select2-container--open .dashboard-filter-dropdown:visible').screenshot(
                path=str(artifacts / (mode + '-dropdown.png')))
        timings.append(dict(result, mode=mode, action=label))
        page.keyboard.press('Escape')
        return result

    try:
        initial = load()
        assert not requests, 'Large catalogues must remain lazy'
        assert measure_open('paged-50', 'cold-open')['rows'] == 50
        for index in range(3):
            assert measure_open('paged-50', 'warm-open-%s' % (index + 1))['rows'] == 50
        assert len(requests) == 1
        open_filter()
        expect(page.locator(ROWS)).to_have_count(50)
        page.keyboard.press('ArrowDown')
        expect(page.locator(ROWS + '.select2-results__option--highlighted')).to_contain_text('cohort 0001')
        scroll_all(1367)
        rendered = page.locator(ROWS).evaluate_all("""nodes => nodes.map(node => ({
            text: node.querySelector('strong').textContent, counts: node.querySelector('small').textContent
        }))""")
        expected = catalogue({})['results']
        assert [row['text'] for row in rendered] == [entry['text'] for entry in expected]
        for row, entry in zip(rendered, expected):
            assert row['counts'] == '%s %s · %s %s' % (
                entry['studyCount'], 'study' if entry['studyCount'] == 1 else 'studies',
                entry['publicationCount'], 'publication' if entry['publicationCount'] == 1 else 'publications')
        assert len(requests) == 1
        checks.append('1,367-entry catalogue renders 50 initial rows and scrolls all ordered counts without additional HTTP')
        search('1366')
        expect(page.locator(ROWS)).to_have_count(1)
        expect(page.locator(ROWS)).to_contain_text('baseline initial cohort 1366')
        assert len(requests) == 1
        page.keyboard.press('Enter')
        page.wait_for_function(READY, timeout=30000)
        assert page.evaluate('gwasDashboardState.capture().cohorts') == ['alpha']
        assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 48
        checks.append('keyboard navigation and local search can select a real cohort beyond the initial page')

        assert page.evaluate('(state) => gwasDashboardState.restore(state)', dict(initial, funders=['funder-one']))
        page.wait_for_function(READY, timeout=30000)
        open_filter()
        expect(page.locator(ROWS)).to_have_count(50)
        expect(page.locator(ROWS).first).to_contain_text('funder-one initial')
        assert len(requests) == 2
        page.keyboard.press('Escape')
        page.locator('label[for="cb2"]').click()
        page.wait_for_function(READY, timeout=30000)
        open_filter()
        expect(page.locator(ROWS).first).to_contain_text('funder-one replication')
        assert len(requests) == 3
        checks.append('large catalogue caches remain isolated by opposite selection and study stage')

        load(['funder-one', 'funder-two'])
        before = len(requests)
        open_filter()
        expect(page.locator(ROWS)).to_have_count(50)
        scroll_all(123)
        pages = [int(query.get('page', ['1'])[0]) for query in requests[before:]]
        assert pages == [1, 2, 3], pages
        assert page.locator(ROWS).last.inner_text().startswith('funder-one,funder-two initial cohort 0122')
        checks.append('multi-funder partial responses retain server pagination including the final 23-row page')

        # Cold typing shares a complete in-flight catalogue rather than fetching again.
        load()
        before = len(requests)
        hold_next = True
        open_filter(); wait_held()
        search('136'); search('1366')
        assert len(requests) == before + 1
        release()
        expect(page.locator(ROWS)).to_have_count(1)
        expect(page.locator(ROWS)).to_contain_text('cohort 1366')
        assert len(requests) == before + 1
        checks.append('rapid cold typing coalesces with one pending complete catalogue and renders the latest search')

        load(['funder-one', 'funder-two'])
        before = len(requests)
        hold_next = True
        open_filter(); wait_held(); search('0122'); release()
        expect(page.locator(ROWS)).to_have_count(1)
        expect(page.locator(ROWS)).to_contain_text('cohort 0122')
        assert len(requests) == before + 2 and requests[-1]['search'] == ['0122']
        checks.append('typing into a pending partial catalogue falls back to the correct distinct server search')

        load(['funder-one', 'funder-two'])
        before = len(requests)
        hold_next = True
        open_filter(); wait_held(); search('0122')
        page.locator('#funder-filter').locator('xpath=following-sibling::span').locator('.select2-selection__clear').click()
        page.wait_for_function(READY, timeout=30000)
        release()
        page.wait_for_function('filterOptionRequests.size === 0')
        assert page.locator('.select2-container--open').count() == 0
        assert len(requests) == before + 1, 'Clearing must cancel a waiting typed-search continuation'
        checks.append('clearing a selection cancels pending partial-catalogue search continuations')

        load()
        before = len(requests)
        hold_next = True
        open_filter(); wait_held(); search('1366'); release(409)
        expect(page.locator('#dashboard-dataset-stale')).to_be_visible()
        page.wait_for_function('filterOptionRequests.size === 0')
        assert len(requests) == before + 1
        checks.append('a stale pending catalogue cannot launch another typed-search request')

        # Isolated control: same response and Select2, but hand it all rows as before.
        # This is a rendering-cost comparison, not a historical production benchmark.
        load()
        page.evaluate('facetOptionPages.page = payload => payload')
        assert measure_open('unpaged-rendering-control', 'cold-open')['rows'] == 1367
        for index in range(3):
            assert measure_open('unpaged-rendering-control', 'warm-open-%s' % (index + 1))['rows'] == 1367
        desktop_control = timings[-1]['appearance']
        page.set_viewport_size({'width': 390, 'height': 844})
        load()
        mobile_paged = measure_open('paged-50-mobile390', 'cold-open')
        assert mobile_paged['rows'] == 50
        page.evaluate('facetOptionPages.page = payload => payload')
        mobile_control = measure_open('unpaged-rendering-control-mobile390', 'warm-open')
        assert mobile_control['rows'] == 1367
        assert not errors, 'Unexpected large-facet JavaScript errors: ' + repr(errors)
        assert not external, 'Unexpected large-facet Internet requests: ' + repr(external)
        (artifacts / 'large-facet-timings.json').write_text(json.dumps({
            'kind': 'synthetic-1367-option-rendering-comparison', 'browser': browser.version,
            'note': 'Unpaged control bypasses only local slicing; timings are evidence, not CI thresholds.',
            'measurements': timings}, indent=2) + '\n', encoding='utf-8')
        assert timings[0]['appearance'] == desktop_control, \
            'Paging must preserve dropdown/first-row geometry and styling, including long labels on later pages'
        checks.append('paged and unpaged controls preserve dropdown geometry and first-row styling')
        assert mobile_paged['appearance'] == mobile_control['appearance']
        assert mobile_paged['appearance']['dropdownWidth'] <= 390
        checks.append('390px paged dropdown preserves original mobile geometry and stays within the viewport')
        return checks
    except Exception:
        page.screenshot(path=str(artifacts / 'large-facet-failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'large-facet-trace.zip'))
        context.close()
