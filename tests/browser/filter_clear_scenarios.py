"""Real Select2 removal gestures must not open or query a closed filter."""
from urllib.parse import urlsplit

from playwright.sync_api import expect


READY = 'window.gwasChartData && window.gwasChartData.canExport()'


def run(browser, base, artifacts):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    errors, external, requests, checks = [], [], [], []
    fail_cohorts = False
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        url = urlsplit(route.request.url)
        if url.netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
            return
        if url.path in ('/api/cohorts', '/api/funders'):
            requests.append(route.request.url)
            if fail_cohorts and url.path == '/api/cohorts':
                route.fulfill(status=500, body='Fixture option-loading failure')
                return
        route.continue_()

    context.route('http*://**/*', route_request)

    def load():
        page.goto(base + '/', wait_until='domcontentloaded')
        page.wait_for_function(READY, timeout=30000)
        assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 72

    try:
        for selector, field, ids, label, remaining_count in (
                ('#dataset-filter', 'cohorts', ['alpha', 'beta'], 'Alpha', 48),
                ('#funder-filter', 'funders', ['funder-one', 'funder-two'], 'Funder One', 24)):
            for gesture in ('clear-all', 'last-chip', 'one-of-two'):
                # Restoration does not warm option caches; each gesture starts cold.
                load()
                state = page.evaluate('gwasDashboardState.capture()')
                state[field] = ids[:1] if gesture == 'last-chip' else ids
                assert page.evaluate('(state) => gwasDashboardState.restore(state)', state)
                page.wait_for_function(READY, timeout=30000)
                control = page.locator(selector).locator('xpath=following-sibling::span')
                before = len(requests)
                remove = '.select2-selection__clear' if gesture == 'clear-all' else '.select2-selection__choice__remove'
                control.locator(remove).first.click()
                page.wait_for_function(READY, timeout=30000)
                page.wait_for_timeout(250)  # Also catch Select2's 150 ms delayed query.
                expected = ids[1:] if gesture == 'one-of-two' else []
                assert page.evaluate('(field) => gwasDashboardState.capture()[field]', field) == expected
                assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == (remaining_count if expected else 72)
                assert page.locator('.select2-container--open').count() == 0, 'Removal must leave dropdowns closed'
                assert page.get_by_text('The results could not be loaded.', exact=True).count() == 0
                assert len(requests) == before, 'Removal must not query facet options'
                if not expected:
                    expect(control.locator('.select2-search__field')).to_have_attribute(
                        'placeholder', 'All Cohorts' if field == 'cohorts' else 'All Funders')
                checks.append(field + ' ' + gesture + ' restores the expected chart without an automatic option query')

                # Suppression is temporary: the next deliberate open and selection work.
                control.locator('.select2-search__field').click()
                option = page.locator('.select2-results__option[role="option"]').filter(has_text=label).first
                expect(option).to_be_visible(timeout=30000)
                assert len(requests) == before + 1
                option.click()
                page.wait_for_function(READY, timeout=30000)
                assert ids[0] in page.evaluate('(field) => gwasDashboardState.capture()[field]', field)
                checks.append(field + ' ' + gesture + ' permits a subsequent intentional open and selection')

        load()
        fail_cohorts = True
        page.locator('#dataset-filter').locator('xpath=following-sibling::span').locator('.select2-search__field').click()
        expect(page.get_by_text('The results could not be loaded.', exact=True)).to_be_visible(timeout=30000)
        checks.append('an intentional cohort query still displays genuine HTTP 500 option-loading failures')
        assert not errors, 'Unexpected filter-clear JavaScript errors: ' + repr(errors)
        assert not external, 'Unexpected filter-clear Internet requests: ' + repr(external)
        return checks
    except Exception:
        page.screenshot(path=str(artifacts / 'filter-clear-failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'filter-clear-trace.zip'))
        context.close()
