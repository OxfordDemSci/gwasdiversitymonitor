import re
import unittest
from html.parser import HTMLParser

from flask import render_template

from app import app as flask_app


class FilterInfoParser(HTMLParser):
    """Collect the accessible info buttons and their tooltip copy."""

    def __init__(self):
        super().__init__()
        self.buttons = []
        self.tooltips = {}
        self._active_tooltip = None
        self._tooltip_tag = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get('class', '').split()

        if tag == 'button' and 'funder-toolbar__info-button' in classes:
            self.buttons.append(attributes)

        if attributes.get('role') == 'tooltip':
            self._active_tooltip = attributes.get('id')
            self._tooltip_tag = tag
            self.tooltips[self._active_tooltip] = []

    def handle_data(self, data):
        if self._active_tooltip:
            self.tooltips[self._active_tooltip].append(data)

    def handle_endtag(self, tag):
        if self._active_tooltip and tag == self._tooltip_tag:
            self._active_tooltip = None
            self._tooltip_tag = None


class DashboardFilterToolbarTests(unittest.TestCase):
    def render_dashboard(self):
        with flask_app.test_request_context('/'):
            return render_template(
                'index.html',
                title='Home',
                switches='true',
                ancestries={},
                ancestriesOrdered={},
                parentTerms={},
                traits={},
                summary={},
                bubbleGraph={},
                tsPlot={},
                chloroMap={},
                heatMap={},
                doughnutGraph={},
                plot_versions={},
            )

    def test_compact_filter_region_precedes_dashboard(self):
        html = self.render_dashboard()

        self.assertIn(
            'aria-label="Filter dashboard by funder and cohort"', html
        )
        self.assertLess(
            html.index('class="funder-toolbar"'),
            html.index('class="dashboard row"'),
        )

    def test_initial_page_does_not_prefetch_filter_catalogues(self):
        html = self.render_dashboard()

        self.assertNotIn("prefetchInitialFilterOptions", html)
        self.assertNotIn("prefetchComplementaryFilterOptions", html)
        self.assertIn('class="funder-toolbar__controls"', html)
        self.assertIn('class="funder-toolbar__footer"', html)

    def test_filter_controls_have_accessible_names_and_help(self):
        html = self.render_dashboard()

        for filter_id, label_id, help_id in (
            ('funder-filter', 'funder-filter-label', 'funder-filter-help'),
            ('dataset-filter', 'dataset-filter-label', 'dataset-filter-help'),
        ):
            self.assertIn(f'id="{label_id}"', html)
            self.assertIn(f'for="{filter_id}"', html)
            self.assertIn(f'id="{help_id}"', html)
            self.assertIn(f'aria-labelledby="{label_id}"', html)
            self.assertIn(f'aria-describedby="{help_id}"', html)

        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn('<span>Export view</span>', html)
        self.assertIn('<span>Download selection source data</span>', html)
        self.assertIn('<span>View report</span>', html)

    def test_filter_controls_default_to_all_funders_and_all_cohorts(self):
        html = self.render_dashboard()

        self.assertIn("placeholder: 'All Funders'", html)
        self.assertIn("placeholder: 'All Cohorts'", html)

        # "All" is the label for an empty filter, not a synthetic selection.
        for filter_id in ('funder-filter', 'dataset-filter'):
            select_start = html.index(f'<select id="{filter_id}"')
            select_end = html.index('</select>', select_start)
            self.assertNotIn('<option', html[select_start:select_end])

    def test_full_counting_help_is_keyboard_accessible_and_concise(self):
        parser = FilterInfoParser()
        parser.feed(self.render_dashboard())

        self.assertEqual(len(parser.buttons), 2)
        buttons_by_description = {
            button.get('aria-describedby'): button
            for button in parser.buttons
        }

        expected_tooltips = {
            'funder-counting-tooltip': 'funder',
            'cohort-counting-tooltip': 'cohort',
        }
        self.assertEqual(set(parser.tooltips), set(expected_tooltips))

        for tooltip_id, subject in expected_tooltips.items():
            button = buttons_by_description[tooltip_id]
            self.assertEqual(button.get('type'), 'button')
            self.assertIn('full counting', button.get('aria-label', '').lower())

            tooltip = ' '.join(parser.tooltips[tooltip_id]).strip()
            words = re.findall(r"\b[\w'-]+\b", tooltip)
            self.assertGreaterEqual(len(words), 20)
            self.assertLessEqual(len(words), 75)
            self.assertIn(subject, tooltip.lower())
            self.assertIn('full', tooltip.lower())
            self.assertIn('fractional', tooltip.lower())


if __name__ == '__main__':
    unittest.main()
