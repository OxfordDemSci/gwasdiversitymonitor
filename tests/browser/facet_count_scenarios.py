"""Scoped Select2 counts remain correct while requests are pending or cached."""
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect


READY = 'window.gwasChartData && window.gwasChartData.canExport()'


def run(browser, base, artifacts):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    errors, external, requests, checks = [], [], [], []
    held, target = {}, {}
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        url = urlsplit(route.request.url)
        if url.netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
            return
        if url.path not in ('/api/cohorts', '/api/funders'):
            route.continue_()
            return
        query = parse_qs(url.query)
        response = route.fetch(timeout=15000)
        assert response.ok, 'Fixture facet request must succeed before delaying it'
        entry = {'path': url.path, 'query': query, 'payload': response.json()}
        requests.append(entry)
        if target and url.path == target['path'] and not held and all(
                query.get(key) == [value] for key, value in target['query'].items()):
            held.update(route=route, response=response, payload=entry['payload'])
        else:
            route.fulfill(response=response)

    context.route('http*://**/*', route_request)

    def restore(state):
        assert page.evaluate('(state) => gwasDashboardState.restore(state)', state)
        page.wait_for_function(READY, timeout=30000)

    def open_filter(selector):
        page.locator(selector).locator('xpath=following-sibling::span').locator('.select2-search__field').click()

    def option(label):
        return page.locator('.select2-results__option[role="option"]').filter(has_text=label).first

    def assert_count(label, count, payload=None, identifier=None):
        row = option(label)
        expect(row).to_be_visible(timeout=30000)
        expect(row.locator('small')).to_contain_text(str(count) + ' studies')
        if payload:
            entry = next(entry for entry in payload['results'] if entry['id'] == identifier)
            assert entry['studyCount'] == count
            expect(row.locator('small')).to_have_text(
                '%s studies · %s publications' % (count, entry['publicationCount']))

    def wait_held():
        deadline = time.monotonic() + 15
        while 'response' not in held and time.monotonic() < deadline:
            page.wait_for_timeout(20)
        assert 'response' in held, 'The intended scoped option request was not intercepted'

    def assert_loading_only():
        expect(page.locator('.select2-results .loading-results')).to_be_visible()
        assert page.locator('.select2-results__option[aria-selected]').count() == 0, \
            'Old-scope counts must not remain selectable below Searching'

    try:
        page.goto(base + '/', wait_until='domcontentloaded')
        page.wait_for_function(READY, timeout=30000)
        initial = page.evaluate('gwasDashboardState.capture()')
        assert not requests, 'Facet catalogues must not be eagerly fetched'
        # Warm both baseline lists, which previously remained beneath Searching.
        for selector, label, count in (
                ('#dataset-filter', 'Alpha', 48), ('#funder-filter', 'Funder One', 36)):
            open_filter(selector)
            assert_count(label, count)
            page.keyboard.press('Escape')

        for selector, field, selected, path, opposite, label, identifier in (
                ('#dataset-filter', 'funders', 'funder-one', '/api/cohorts', 'funders', 'Alpha', 'alpha'),
                ('#funder-filter', 'cohorts', 'alpha', '/api/funders', 'cohorts', 'Funder One', 'funder-one')):
            restore(dict(initial, **{field: [selected]}))
            target.clear(); target.update(path=path, query={opposite: selected, 'stage': 'initial'})
            held.clear()
            open_filter(selector)
            wait_held()
            assert_loading_only()
            payload = held['payload']
            held['route'].fulfill(response=held['response'])
            assert_count(label, 24, payload, identifier)
            checks.append(path + ' hides baseline counts while loading and renders exact scoped study/publication counts')
            target.clear()
            page.keyboard.press('Escape')
            before = len(requests)
            open_filter(selector)
            assert_count(label, 24)
            assert len(requests) == before, 'Reopening a cached scope must not request options again'
            page.evaluate("""() => {
                window.__facetOriginalTimeout = window.setTimeout;
                window.__facetSearchDelays = [];
                window.setTimeout = function(fn, delay, ...args) {
                    window.__facetSearchDelays.push(delay);
                    return window.__facetOriginalTimeout(fn, delay, ...args);
                };
            }""")
            try:
                page.locator('.select2-container--open .select2-search__field').fill(label[:2])
                assert_count(label, 24)
                assert len(requests) == before, 'Complete cached scoped lists must support local search'
                assert not page.evaluate('window.__facetSearchDelays.includes(150)'), \
                    'Cached searches must bypass the network-search debounce'
            finally:
                page.evaluate('window.setTimeout = window.__facetOriginalTimeout')
            checks.append(path + ' reopens and searches cached scoped counts without HTTP or the 150ms debounce')
            page.keyboard.press('Escape')

        restore(initial)
        before = len(requests)
        for selector, label, count in (
                ('#dataset-filter', 'Alpha', 48), ('#funder-filter', 'Funder One', 36)):
            open_filter(selector)
            assert_count(label, count)
            page.keyboard.press('Escape')
        assert len(requests) == before
        checks.append('clearing facet scope restores baseline counts from the correctly keyed cache')

        restore(dict(initial, funders=['funder-two']))
        target.update(path='/api/cohorts', query={'funders': 'funder-two', 'stage': 'initial'})
        held.clear()
        open_filter('#dataset-filter')
        wait_held()
        assert_loading_only()
        page.locator('label[for="cb2"]').click()
        page.wait_for_function('gwasDashboardState.capture().stage === "replication"')
        page.wait_for_function(READY, timeout=30000)
        assert page.locator('.select2-container--open').count() == 0
        held['route'].fulfill(response=held['response'])
        page.wait_for_function('filterOptionRequests.size === 0')
        assert page.evaluate("$('#dataset-filter').data('select2').results.$results.find('[aria-selected]').length") == 0
        assert page.locator('.select2-container--open').count() == 0
        target.clear()
        open_filter('#dataset-filter')
        # Funder Two covers indices 4/5 modulo 6; only index 4 is in Alpha.
        assert_count('Alpha', 12)
        assert requests[-1]['query']['stage'] == ['replication']
        checks.append('stage changes close pending searches and late discovery responses cannot repopulate replication options')
        assert not errors, 'Unexpected facet-count JavaScript errors: ' + repr(errors)
        assert not external, 'Unexpected facet-count Internet requests: ' + repr(external)
        return checks
    except Exception:
        page.screenshot(path=str(artifacts / 'facet-count-failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'facet-count-trace.zip'))
        context.close()
