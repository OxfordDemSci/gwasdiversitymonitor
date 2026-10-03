#!/usr/bin/env python3
"""External, read-only health check. A nonzero exit must be wired to an alert."""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def check(base_url, expected_commit=None):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use an HTTPS site URL without credentials')
    if parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Use the site root, without query parameters')
    failures = []
    for endpoint, field in (('live', 'alive'), ('ready', 'ready'), ('data', 'healthy')):
        url = base_url.rstrip('/') + '/health/' + endpoint
        try:
            request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read(32769)
                if len(body) > 32768:
                    raise ValueError('oversized health response')
                payload = json.loads(body)
                if response.status != 200 or payload.get(field) is not True:
                    raise ValueError('unhealthy response')
                if 'no-store' not in response.headers.get('Cache-Control', ''):
                    raise ValueError('health response is cacheable')
                if expected_commit and payload.get('application', {}).get('commit') != expected_commit:
                    raise ValueError('unexpected deployed commit')
            print(endpoint + ': OK')
        except (OSError, ValueError, TypeError) as error:
            failures.append(endpoint)
            print(endpoint + ': FAILED (' + type(error).__name__ + ')', file=sys.stderr)
    return not failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--expected-commit')
    arguments = parser.parse_args()
    try:
        sys.exit(0 if check(arguments.url, arguments.expected_commit) else 1)
    except ValueError as error:
        parser.error(str(error))
