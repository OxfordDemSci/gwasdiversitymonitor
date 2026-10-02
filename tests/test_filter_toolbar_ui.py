import unittest

from flask import render_template

from app import app as flask_app


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
            )

    def test_prominent_filter_region_precedes_dashboard(self):
        html = self.render_dashboard()

        self.assertIn(
            'aria-labelledby="dashboard-filter-title"', html
        )
        self.assertIn(
            '<h2 id="dashboard-filter-title">'
            'Explore GWAS by funder and cohort</h2>',
            html,
        )
        self.assertLess(
            html.index('id="dashboard-filter-title"'),
            html.index('class="dashboard row"'),
        )
        self.assertIn('class="funder-toolbar__controls"', html)
        self.assertIn('class="funder-toolbar__footer"', html)

    def test_filter_controls_have_visible_names_and_help(self):
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
        self.assertIn('<span>Download data</span>', html)
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


if __name__ == '__main__':
    unittest.main()
