"""Delayed real responses exercise selection and figure-export revision guards."""
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect


READY = 'window.gwasChartData && window.gwasChartData.canExport()'


def _wait_held(page, held):
    """Pump browser events until the intercepted upstream response is available."""
    deadline = time.monotonic() + 15
    while 'response' not in held and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert 'response' in held, 'The intended delayed response was never intercepted'
    assert held['response'].ok, 'The fixture upstream response must succeed before delaying it'


def _context(browser, base, intercept):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    errors, external = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
        elif not intercept(route):
            route.continue_()

    context.route('http*://**/*', route_request)
    return context, page, errors, external


def _load_fixture(page, base):
    page.goto(base + '/', wait_until='domcontentloaded')
    page.wait_for_function(READY, timeout=30000)
    assert page.evaluate("""() => {
        const chart = gwasChartData.snapshot('bubbleGraph');
        return chart.rowCount === 72 && chart.rowAt(0).ACCESSION.startsWith('GCSTFIX');
    }"""), 'Race scenarios may only run against the synthetic browser fixture'


def _selection_race(browser, base, artifacts=None):
    held = {}

    def delay_first_selection(route):
        url = urlsplit(route.request.url)
        funders = parse_qs(url.query).get('funders', [])
        if url.path == '/json/filtered-dashboard.json' and funders == ['funder-one'] and not held:
            held['route'] = route
            held['response'] = route.fetch(timeout=10000)
            return True
        return False

    context, page, errors, external = _context(browser, base, delay_first_selection)
    if artifacts:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    try:
        _load_fixture(page, base)
        initial = page.evaluate('gwasDashboardState.capture()')
        first = dict(initial, funders=['funder-one'], cohorts=[])
        second = dict(initial, funders=['funder-two'], cohorts=[])
        page.evaluate("""state => {
            window.__firstRaceRestore = {settled: false};
            void gwasDashboardState.restore(state).then(success => {
                window.__firstRaceRestore = {settled: true, success};
            }, error => {
                window.__firstRaceRestore = {settled: true, error: String(error)};
            });
        }""", first)
        _wait_held(page, held)
        assert not page.evaluate('window.__firstRaceRestore.settled'), 'First selection must remain pending'
        assert not page.evaluate('gwasChartData.canExport()'), 'Pending selection must block export'

        page.evaluate("""state => {
            window.__secondRaceRestore = {settled: false};
            void gwasDashboardState.restore(state).then(success => {
                window.__secondRaceRestore = {settled: true, success};
            }, error => {
                window.__secondRaceRestore = {settled: true, error: String(error)};
            });
        }""", second)
        page.wait_for_function('window.__secondRaceRestore.settled', timeout=30000)
        assert page.evaluate('window.__secondRaceRestore.success'), 'Newer selection must settle first'
        page.wait_for_function(READY, timeout=30000)
        assert page.evaluate('gwasDashboardState.capture().funders') == ['funder-two']
        assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 24

        # Deliver a successful obsolete response, rather than replacing it with
        # a failure: its complete payload must be ignored by both revision guards.
        held['route'].fulfill(response=held['response'])
        page.wait_for_function('window.__firstRaceRestore.settled', timeout=10000)
        assert page.evaluate('window.__firstRaceRestore.success') is False
        page.wait_for_function(READY, timeout=10000)
        assert page.evaluate('gwasDashboardState.capture().funders') == ['funder-two']
        assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 24
        assert parse_qs(urlsplit(page.url).query).get('funders') == ['funder-two']
        assert not errors, 'Unexpected selection-race JavaScript errors: ' + repr(errors)
        assert not external, 'Unexpected selection-race Internet requests: ' + repr(external)
        return 'late successful selection response cannot replace the newer funder view or export data'
    except Exception:
        if artifacts:
            page.screenshot(path=str(artifacts / 'selection-race-failure.png'), full_page=True)
        raise
    finally:
        if artifacts:
            context.tracing.stop(path=str(artifacts / 'selection-race-trace.zip'))
        context.close()


def _image_export_race(browser, base, artifacts=None):
    held = {}

    def delay_export_library(route):
        if urlsplit(route.request.url).path == '/static/js/dashboard-export.js' and not held:
            held['route'] = route
            held['response'] = route.fetch(timeout=10000)
            return True
        return False

    context, page, errors, external = _context(browser, base, delay_export_library)
    if artifacts:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    downloads = []
    page.on('download', lambda download: downloads.append(download.suggested_filename))
    try:
        _load_fixture(page, base)
        assert page.evaluate('gwasDashboardState.capture().metric') == 'participants'
        page.evaluate("""() => {
            window.__imageRaceExport = {settled: false};
            void downloadImage('#heatMap', 'heatmapSVG', false).then(() => {
                window.__imageRaceExport = {settled: true, success: true};
            }, error => {
                window.__imageRaceExport = {settled: true, success: false, error: error.message};
            });
        }""")
        _wait_held(page, held)
        assert not page.evaluate('window.__imageRaceExport.settled'), 'Figure must wait for its lazy export library'
        page.locator('label[for="cb1"]').click()
        page.wait_for_function('gwasDashboardState.capture().metric === "studies" && ' + READY, timeout=30000)

        held['route'].fulfill(response=held['response'])
        page.wait_for_function('window.__imageRaceExport.settled', timeout=10000)
        outcome = page.evaluate('window.__imageRaceExport')
        assert outcome['success'] is False, 'A changed view must cancel the captured figure'
        assert 'changed' in outcome['error'].lower(), outcome
        expect(page.locator('#dashboard-share-status')).to_be_visible()
        expect(page.locator('#dashboard-share-status')).to_contain_text('retry')
        # Drain the browser's next render cycle so any download dispatched by the
        # settled export would be delivered; no arbitrary network-idle sleep.
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert not downloads, 'A cancelled figure must not download an old or mixed-view bundle: ' + repr(downloads)
        assert not errors, 'Unexpected figure-race JavaScript errors: ' + repr(errors)
        assert not external, 'Unexpected figure-race Internet requests: ' + repr(external)
        return 'changing metric during lazy image export cancels the download and explains how to retry'
    except Exception:
        if artifacts:
            page.screenshot(path=str(artifacts / 'image-race-failure.png'), full_page=True)
        raise
    finally:
        if artifacts:
            context.tracing.stop(path=str(artifacts / 'image-race-trace.zip'))
        context.close()


def run(browser, base, artifacts=None):
    """Return checks for the temporary fixture server created by the main runner."""
    address = urlsplit(base)
    assert address.scheme == 'http' and address.hostname in {'127.0.0.1', 'localhost'}, \
        'Race scenarios require the harness-owned local fixture server'
    return [_selection_race(browser, base, artifacts), _image_export_race(browser, base, artifacts)]
