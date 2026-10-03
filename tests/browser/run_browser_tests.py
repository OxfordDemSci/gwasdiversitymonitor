"""Real Chromium smoke/regression tests against an isolated synthetic release."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
import zipfile

from playwright.sync_api import expect, sync_playwright

from fixture_data import REPOSITORY
from filter_clear_scenarios import run as run_filter_clear_scenarios
from facet_count_scenarios import run as run_facet_count_scenarios
from race_scenarios import run as run_race_scenarios

sys.path.insert(0, str(REPOSITORY / 'scripts/performance'))
from browser_metrics import READY, measure


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_server(process, url):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Fixture server exited before readiness; inspect server.log')
        try:
            with urllib.request.urlopen(url + '/api/provenance', timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.1)
    raise TimeoutError('Fixture server did not start within 30 seconds')


def ready(page):
    page.wait_for_function(READY, timeout=30000)


def restore(page, state):
    assert page.evaluate('(state) => gwasDashboardState.restore(state)', state), 'Shared-view restore failed'
    ready(page)


def select_facet(page, selector, term, label):
    page.locator(selector).locator('xpath=following-sibling::span').click()
    search = page.locator('.select2-container--open input.select2-search__field')
    search.fill(term)
    page.locator('.select2-results__option[role="option"]').filter(has_text=label).first.click()
    ready(page)


def check_ancestry_panel(page, artifacts, check, *, mobile=False):
    """Keep native ancestry buttons visually equivalent to the original cards."""
    panel = page.locator('#bubbleGraph .ancestry-filter')
    panel.screenshot(path=str(artifacts / ('mobile-ancestry.png' if mobile else 'desktop-ancestry.png')))
    appearance = panel.evaluate("""panel => {
      const rect = node => { const r = node.getBoundingClientRect();
        return {width:r.width, height:r.height, left:r.left, right:r.right, centerY:r.y+r.height/2}; };
      const style = getComputedStyle(panel);
      const all = panel.querySelector('.option.all'), allStyle = getComputedStyle(all);
      return {width:panel.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),
        all:{...rect(all), color:allStyle.color, transform:allStyle.textTransform, align:allStyle.textAlign},
        cards:[...panel.querySelectorAll('.option.btn')].map(node => {
          const s = getComputedStyle(node), dot = node.querySelector('.circle'), d = getComputedStyle(dot);
          return {...rect(node), background:s.backgroundColor,
            dot:{...rect(dot), background:d.backgroundColor, radius:d.borderRadius, position:d.position}};
        })};
    }""")
    label = 'mobile' if mobile else 'desktop'
    cards = appearance['cards']
    check(len(cards) >= 4 and all(card['background'] == 'rgb(255, 255, 255)' for card in cards),
          label + ' ancestry controls retain white cards')
    check(all(abs(card['dot']['width'] - 10) < .5 and abs(card['dot']['height'] - 10) < .5
              and card['dot']['position'] == 'absolute' and card['dot']['radius'] == '50%'
              and card['dot']['background'] not in ('transparent', 'rgba(0, 0, 0, 0)') for card in cards),
          label + ' ancestry color dots remain visible 10px circles')
    check(appearance['all']['color'] == 'rgb(255, 255, 255)'
          and appearance['all']['transform'] == 'uppercase' and appearance['all']['align'] == 'center',
          label + ' View all retains white centered uppercase styling')
    check(max(card['width'] for card in cards) - min(card['width'] for card in cards) < 2,
          label + ' ancestry cards have equal widths')
    if mobile:
        check(all(abs(card['width'] - .48 * appearance['width']) < 2 for card in cards)
              and cards[1]['left'] > cards[0]['right']
              and abs(cards[0]['centerY'] - cards[1]['centerY']) < 2
              and cards[2]['centerY'] > cards[0]['centerY'] + 10
              and abs(appearance['all']['width'] - appearance['width']) < 2,
              '390px ancestry panel retains two columns and full-width View all')
    else:
        check(all(abs(card['width'] - appearance['width']) < 2 for card in cards),
              '1440px ancestry cards fill the sidebar width')


def check_zip(download, directory, *, image=None):
    path = directory / download.suggested_filename
    download.save_as(path)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert {'view.json', 'provenance.json', 'README.txt', 'CITATION.txt'} <= set(archive.namelist())
        view = json.loads(archive.read('view.json'))
        provenance = json.loads(archive.read('provenance.json'))
        for chart in view['charts']:
            rows = list(csv.DictReader(io.StringIO(archive.read(chart['id'] + '.csv').decode('utf-8'))))
            assert len(rows) == chart['rowCount']
        if image:
            content = archive.read(image)
            if image.endswith('.svg'):
                assert b'gwas-export-metadata' in content
                assert provenance['datasetId'].encode() in content
            else:
                assert content.startswith(b'\x89PNG\r\n\x1a\n')
        return {'name': path.name, 'bytes': path.stat().st_size, 'charts': view['charts'],
                'datasetId': provenance['datasetId']}


def scenario(browser, base, fixture, artifacts):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    errors, external, checks, request_failures = [], [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: request_failures.append('%s %s' % (response.status, response.url))
            if response.status >= 400 else None)
    page.on('requestfailed', lambda request: request_failures.append(request.url + ': ' + str(request.failure))
            if request.failure != 'net::ERR_ABORTED' else None)
    def local_only(route):
        if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    context.route('http*://**/*', local_only)
    def check(condition, label):
        assert condition, label
        checks.append(label)
    try:
        metrics = measure(page, base + '/')
        budgets = json.loads((Path(__file__).with_name('budgets.json')).read_text())
        for key in ('staticEncodedBytes', 'plotEncodedBytes', 'resourceEncodedBytes', 'readyMs'):
            check(metrics[key] <= budgets[key], key + ' budget')
        check(len(metrics['resources']) <= budgets['maxResourceRequests'], 'initial request-count budget')
        paths = [resource['path'] for resource in metrics['resources']]
        check(all(paths.count(path) == 1 for path in paths if path.startswith('/json/')), 'initial plots are requested only once')
        check(any(row['encodedBytes'] < row['decodedBytes'] for row in metrics['resources']
                  if row['path'] == '/static/js/imports/d3.v4.min.js'), 'real gzip static responses are measured')
        check(not any('/api/' in path or 'dashboard-export.js' in path or 'd3plus-text' in path for path in paths),
              'no eager comparison, facets, metadata coverage, examples, or export library')
        check(metrics['datasetId'] == fixture['datasetId'], 'fixture dataset identity served')
        check_ancestry_panel(page, artifacts, check)
        ancestry = page.locator('#bubbleGraph .ancestry-filter .option.btn').first
        ancestry.focus(); page.keyboard.press('Enter')
        expect(ancestry).to_have_attribute('aria-pressed', 'false')
        check(page.locator('#bubbleGraph .ancestry-filter .option.btn[aria-pressed="false"]').count() == 1,
              'Enter excludes exactly one ancestry')
        page.locator('#bubbleGraph .ancestry-filter .option.all').focus()
        page.keyboard.press('Enter')
        expect(ancestry).to_have_attribute('aria-pressed', 'true')
        ready(page)
        check(page.locator('#bubbleGraph .ancestry-filter .option.btn[aria-pressed="false"]').count() == 0
              and page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == fixture['bubbleRowsPerStage'],
              'keyboard View all restores every ancestry and the original bubble count')
        check(metrics['bubbleRows'] == fixture['bubbleRowsPerStage'], 'all fixture discovery bubbles plotted')
        check(page.locator('#timeSeriesSVG, #worldMapSVG, #bubbleCanvas, #heatmapSVG, #doughnutSVG').count() == 5,
              'all five chart surfaces render')
        chart_values = page.evaluate("""() => {
          const rows = id => { const s = gwasChartData.snapshot(id); return Array.from({length:s.rowCount}, (_, i) => s.rowAt(i)); };
          return {bubble: rows('bubbleGraph')[0],
            series: rows('timeSeries').find(r => r.year === 2021 && r.ancestry === 'Asian'),
            heat: rows('heatMap').find(r => r.ancestry === 'Asian' && r.parentTerm === 'metabolic disease'),
            map: rows('worldMap')[0], doughnut: rows('doughnutGraph').find(r => r.ancestry === 'Asian'),
            marks: {heat: [...document.querySelectorAll('#heatmapSVG rect')].filter(n => n.__data__ && n.__data__.ancestry).length,
              series: document.querySelectorAll('#timeSeriesSVG circle.dot').length,
              doughnut: [...document.querySelectorAll('#doughnutSVG path')].filter(n => n.__data__ && n.__data__.data).length}};
        }""")
        check(chart_values['bubble']['N'] == 100, 'bubble provider preserves known participant count')
        check(abs(chart_values['series']['percentage'] - 32.84313725) < 1e-8, 'time series preserves known fixture percentage')
        check(chart_values['heat']['count'] == 1608, 'heat map preserves known fixture count')
        check(chart_values['map']['country'] == 'United Kingdom' and chart_values['map']['count'] == 4896,
              'world map joins known recruitment value')
        check(abs(chart_values['doughnut']['percentage'] - 32.84313725) < 1e-8, 'doughnut preserves known fixture percentage')
        check(chart_values['marks']['heat'] == 12 and chart_values['marks']['series'] > 0 and chart_values['marks']['doughnut'] > 0,
              'non-bubble charts have real bound data marks')
        check(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'desktop has no horizontal page overflow')
        # Accessible table: native keyboard open, bounded rows, Escape/focus return.
        button = page.locator('[data-chart-table="bubbleGraph"]')
        button.focus(); page.keyboard.press('Enter')
        expect(page.locator('#dashboard-table-dialog')).to_be_visible()
        check(page.locator('#dashboard-table-body tr').count() == 50, 'table reads one 50-row page')
        page.locator('#dashboard-table-next').click()
        expect(page.locator('#dashboard-table-range')).to_contain_text('51–72')
        check(page.locator('#dashboard-table-body tr').count() == 22, 'table second page has remaining rows')
        page.keyboard.press('Escape')
        expect(button).to_be_focused()
        checks.append('table keyboard Escape restores invoking control')
        # Versioned shared view through refresh and real browser history.
        initial = page.evaluate('gwasDashboardState.capture()')
        page.locator('label[for="cb1"]').click()
        page.wait_for_function('location.search.includes("metric=studies")')
        page.locator('label[for="cb2"]').click()
        page.wait_for_function('location.search.includes("stage=replication")')
        shared_url = page.url
        page.reload(); ready(page)
        check(page.evaluate('gwasDashboardState.capture().metric') == 'studies', 'metric survives shared-view refresh')
        check(page.evaluate('gwasDashboardState.capture().stage') == 'replication', 'stage survives shared-view refresh')
        page.go_back(); ready(page)
        check(page.evaluate('gwasDashboardState.capture().stage') == 'discovery', 'Back restores previous view')
        page.go_forward(); ready(page)
        check(page.url == shared_url, 'Forward restores shared URL')
        restore(page, initial)
        # Genuine select2 search routes and filtered aggregation.
        select_facet(page, '#funder-filter', 'Funder One', 'Funder One')
        page.wait_for_function('gwasDashboardState.capture().funders.includes("funder-one")')
        check(page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 36, 'funder selection redraws actual subset')
        select_facet(page, '#dataset-filter', 'Alpha', 'Alpha')
        page.wait_for_function('gwasDashboardState.capture().cohorts.includes("alpha")')
        check(page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 24, 'cohort and funder intersect exactly')
        # Comparison is requested only after an explicit run, with same-side overlap.
        page.locator('#dashboard-compare-open').click()
        page.locator('#comparison-submit').click()
        expect(page.locator('#comparison-results')).to_be_visible(timeout=30000)
        expect(page.locator('#comparison-overlap')).to_contain_text('Shared')
        check(page.evaluate('gwasChartData.ids().includes("comparison")'), 'completed comparison provides export data')
        comparison = page.evaluate('gwasChartData.snapshot("comparison").settings')
        check(comparison['left']['studyCount'] == 24 and comparison['right']['studyCount'] == 72
              and comparison['overlap']['studyCount'] == 24, 'comparison counts actual selections and shared accessions')
        page.locator('#comparison-close').click()
        restore(page, initial)
        # ZIP integrity/read-back independently checks exact row counts and identity.
        page.locator('#dashboard-export-open').click()
        with page.expect_download() as download:
            page.locator('#dashboard-export-submit').click()
        bundle = check_zip(download.value, artifacts)
        check(len(bundle['charts']) == 5 and bundle['datasetId'] == fixture['datasetId'], 'five-chart ZIP carries exact data and loaded identity')
        page.locator('#dashboard-export-close').click()
        for png, image in ((False, 'heatMap.svg'), (True, 'heatMap.png')):
            with page.expect_download() as download:
                page.evaluate('(png) => downloadImage("#heatMap", "heatmapSVG", png)', png)
            check_zip(download.value, artifacts, image=image)
            checks.append(image + ' figure bundle is valid')
        # Metadata coverage/examples remain user-triggered and work offline from the Internet.
        page.locator('#dashboard-provenance summary').click()
        expect(page.locator('#provenance-coverage-values')).to_be_visible(timeout=30000)
        page.locator('#dashboard-examples summary').click()
        expect(page.locator('#dashboard-example-buttons button')).to_have_count(3)
        checks.append('on-demand coverage and recorded examples load')
        # A stale table cannot keep offering old rows under new settings.
        page.locator('[data-chart-table="heatMap"]').click()
        page.evaluate('gwasChartData.changed("heatMap")')
        check(page.locator('#dashboard-table-body tr').count() == 0, 'table invalidation clears old rows')
        page.locator('#dashboard-table-refresh').click()
        check(page.locator('#dashboard-table-body tr').count() > 0, 'table refresh captures current view')
        page.keyboard.press('Escape')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(400)  # Debounced responsive redraw (180 ms), not network readiness.
        ready(page)
        check(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), '390px mobile viewport has no page overflow')
        check_ancestry_panel(page, artifacts, check, mobile=True)
        page.locator('[data-chart-table="bubbleGraph"]').click()
        check(page.evaluate('document.getElementById("dashboard-table-dialog").getBoundingClientRect().width <= innerWidth'),
              'mobile table stays within viewport')
        page.screenshot(path=str(artifacts / 'mobile-table.png'))
        page.keyboard.press('Escape')
        check(not errors, 'no unexpected JavaScript errors: ' + repr(errors))
        check(not external, 'no Internet dependencies: ' + repr(external))
        check(not request_failures, 'no unexpected failed HTTP resources: ' + repr(request_failures))
        return {'checks': checks, 'metrics': metrics, 'bundle': bundle}
    except Exception:
        page.screenshot(path=str(artifacts / 'failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'trace.zip'))
        context.close()


def failure_scenarios(browser, base, artifacts):
    """Network faults use browser interception, never production-only test routes."""
    checks = []
    errors, external = [], []
    def guard(route):
        if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    context.route('http*://**/*', guard)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    failed = False
    def fail_heat(route):
        nonlocal failed
        if not failed:
            failed = True
            route.fulfill(status=503, body='fixture transient failure')
        else:
            route.continue_()
    context.route('**/json/heatMap.json?*', fail_heat)
    try:
        page.goto(base + '/', wait_until='domcontentloaded')
        retry = page.locator('#heatMap .dashboard-load-status button')
        expect(retry).to_be_visible(timeout=30000)
        check = page.evaluate('gwasChartData.canExport()')
        assert not check, 'Initial failed chart must prevent export'
        retry.click(); ready(page)
        checks.append('transient chart failure has a working retry and gates exports')
        # Fail a facet restore, then recover the same intended selection.
        context.route('**/json/filtered-dashboard.json?*', lambda route: route.fulfill(status=503, body='fixture selection failure'))
        state = page.evaluate('gwasDashboardState.capture()')
        state['funders'] = ['funder-one']
        assert page.evaluate('(state) => gwasDashboardState.restore(state)', state) is False
        expect(page.locator('#dashboard-filter-retry')).to_be_visible()
        assert not page.evaluate('gwasChartData.canExport()')
        context.unroute('**/json/filtered-dashboard.json?*')
        page.locator('#dashboard-filter-retry').click(); ready(page)
        assert page.evaluate('gwasChartData.snapshot("bubbleGraph").rowCount') == 36
        checks.append('failed selection keeps exports blocked until its retry succeeds')
        assert not errors and not external, repr((errors, external))
    except Exception:
        page.screenshot(path=str(artifacts / 'retry-failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'retry-trace.zip'))
        context.close()
    context = browser.new_context()
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    context.route('http*://**/*', guard)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    def mismatched_identity(route):
        response = route.fetch()
        headers = dict(response.headers)
        headers['x-gwas-dataset-id'] = 'gwas-' + 'f' * 64
        route.fulfill(response=response, headers=headers)
    context.route('**/json/heatMap.json?*', mismatched_identity)
    try:
        page.goto(base + '/', wait_until='domcontentloaded')
        expect(page.locator('#dashboard-dataset-stale')).to_be_visible(timeout=30000)
        assert not page.evaluate('gwasChartData.canExport()')
        checks.append('mismatched successful response identity blocks mixed-release exports')
        assert not errors and not external, repr((errors, external))
    except Exception:
        page.screenshot(path=str(artifacts / 'stale-failure.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(artifacts / 'stale-trace.zip'))
        context.close()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, help='Failure screenshots, trace, metrics and server log directory')
    args = parser.parse_args()
    artifacts = (args.artifacts or Path(tempfile.mkdtemp(prefix='gwas-browser-results-'))).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gwas-browser-fixture-') as directory:
        port = unused_port()
        base = 'http://127.0.0.1:' + str(port)
        environment = dict(os.environ, GWAS_PORT=str(port), GWAS_HOST='127.0.0.1',
                           PYTHONDONTWRITEBYTECODE='1', GWAS_NOINDEX='1', GOATCOUNTER_URL='')
        with (artifacts / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, str(Path(__file__).with_name('fixture_server.py'))],
                                      cwd=directory, env=environment, stdout=log, stderr=log,
                                      start_new_session=True)
            try:
                wait_server(server, base)
                fixture = json.loads((Path(directory) / 'fixture.json').read_text())
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        result = scenario(browser, base, fixture, artifacts)
                        result['checks'].extend(failure_scenarios(browser, base, artifacts))
                        result['checks'].extend(run_race_scenarios(browser, base, artifacts))
                        result['checks'].extend(run_filter_clear_scenarios(browser, base, artifacts))
                        result['checks'].extend(run_facet_count_scenarios(browser, base, artifacts))
                    finally:
                        browser.close()
                result['kind'] = 'synthetic-fixture-regression-test'
                (artifacts / 'results.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
                print('PASS: %d browser checks; artifacts: %s' % (len(result['checks']), artifacts))
                print(json.dumps({key: result['metrics'][key] for key in
                                  ('readyMs', 'staticEncodedBytes', 'plotEncodedBytes', 'resourceEncodedBytes')}))
            finally:
                if server.poll() is None:
                    os.killpg(server.pid, signal.SIGTERM)
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(server.pid, signal.SIGKILL)
                        server.wait(timeout=5)


if __name__ == '__main__':
    main()
