"""Cold-browser metrics; production measurements stay separate from fixture CI."""
import argparse
import json
import math
from pathlib import Path
import statistics

from playwright.sync_api import sync_playwright


READY = 'window.gwasChartData && window.gwasChartData.canExport()'
INSTRUMENT = """() => {
  window.__gwasMetrics = {longTasks: [], lcp: null};
  new PerformanceObserver(list => list.getEntries().forEach(e =>
    __gwasMetrics.longTasks.push({start: e.startTime, duration: e.duration})))
    .observe({entryTypes: ['longtask']});
  new PerformanceObserver(list => list.getEntries().forEach(e =>
    __gwasMetrics.lcp = e.startTime)).observe({type: 'largest-contentful-paint', buffered: true});
}"""


def measure(page, url, *, cpu=1, mbps=0, timeout=60000):
    client = page.context.new_cdp_session(page)
    client.send('Network.enable')
    client.send('Network.setCacheDisabled', {'cacheDisabled': True})
    client.send('Emulation.setCPUThrottlingRate', {'rate': cpu})
    if mbps:
        client.send('Network.emulateNetworkConditions', {
            'offline': False, 'latency': 40, 'downloadThroughput': mbps * 1000000 / 8,
            'uploadThroughput': mbps * 1000000 / 8,
        })
    page.add_init_script('(' + INSTRUMENT + ')();')
    page.goto(url, wait_until='domcontentloaded', timeout=timeout)
    page.wait_for_function(READY, timeout=timeout)
    chart_ready_ms = page.evaluate('performance.now()')
    # Do not undercount initial fonts/images that finish just after charts.
    page.wait_for_load_state('networkidle', timeout=timeout)
    result = page.evaluate("""() => {
      const resources = performance.getEntriesByType('resource').map(r => ({
        path: new URL(r.name).pathname, encodedBytes: r.encodedBodySize,
        decodedBytes: r.decodedBodySize, durationMs: r.duration}));
      const sum = prefix => resources.filter(r => r.path.startsWith(prefix))
        .reduce((total, r) => total + r.encodedBytes, 0);
      return {readyMs: performance.now(), lcpMs: __gwasMetrics.lcp,
        longTasks: __gwasMetrics.longTasks, resources,
        staticEncodedBytes: sum('/static/'), plotEncodedBytes: sum('/json/'),
        resourceEncodedBytes: resources.reduce((n, r) => n + r.encodedBytes, 0),
        datasetId: gwasProvenance.loaded.datasetId,
        bubbleRows: gwasChartData.snapshot('bubbleGraph').rowCount};
    }""")
    result.update(readyMs=chart_ready_ms, cpuSlowdown=cpu, downloadMbps=mbps, viewport=page.viewport_size,
                  browserVersion=page.context.browser.version)
    # Keep the subsequent interaction tests unthrottled and normally cached.
    client.send('Emulation.setCPUThrottlingRate', {'rate': 1})
    client.send('Network.emulateNetworkConditions', {
        'offline': False, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1,
    })
    client.send('Network.setCacheDisabled', {'cacheDisabled': False})
    client.detach()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Explicit existing dashboard URL; never starts a real-data server')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--cpu', type=float, default=4)
    parser.add_argument('--mbps', type=float, default=10)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 20 or args.cpu < 1 or args.mbps < 0:
        parser.error('Use 1–20 runs, CPU slowdown >=1 and Mbps >=0')
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for _ in range(args.runs):
                context = browser.new_context(viewport={'width': args.width, 'height': 1000})
                try:
                    results.append(measure(context.new_page(), args.url, cpu=args.cpu, mbps=args.mbps))
                finally:
                    context.close()
        finally:
            browser.close()
    timings = sorted(result['readyMs'] for result in results)
    report = {'kind': 'explicit-url-cold-browser-measurement', 'url': args.url, 'runs': results,
              'summary': {'runs': len(timings), 'medianReadyMs': statistics.median(timings),
                          'p95ReadyMsNearestRank': timings[math.ceil(.95 * len(timings)) - 1],
                          'minReadyMs': timings[0], 'maxReadyMs': timings[-1]},
              'note': 'Browser cache is cold; server cache warmth and local hardware vary. This is not the synthetic CI fixture.'}
    content = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(content, encoding='utf-8')
    else:
        print(content)


if __name__ == '__main__':
    main()
