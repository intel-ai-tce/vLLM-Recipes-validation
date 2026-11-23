"""Tests for the optional multi-release major report."""
import json
import tempfile
import unittest
from pathlib import Path

from compare_major_range import choose_runs, generate_range, metric_cells


def run(version, n, throughput=100.0, status='PASS'):
    model = {'model': 'example', 'status': status,
             'metrics': {'output_throughput': throughput, 'p99_ttft_ms': 1000 / throughput,
                         'p99_tpot_ms': 1000 / throughput}}
    return {'metadata': {'version': version, 'image_override': '', 'model': '',
                         'bench_num_warmups': '3', 'bench_num_prompts': '100'},
            'summary': {'run_id': f'major-{n}', 'run_date': '2026-11-19',
                        'tested': 1, 'passed': int(status == 'PASS'),
                        'failed': int(status != 'PASS'), 'models': [model]},
            'run_id': str(n), 'url': f'https://github.com/org/repo/actions/runs/{n}'}


class RangeTests(unittest.TestCase):
    def test_range_selection_and_semver(self):
        runs = [run('v0.34.0', 34), run('v0.32.0', 32), run('v0.33.0', 33)]
        self.assertEqual([x['metadata']['version'] for x in choose_runs(runs, 'v0.32.0', 'v0.34.0')],
                         ['v0.32.0', 'v0.33.0', 'v0.34.0'])
        self.assertEqual(len(choose_runs(runs)), 2)

    def test_unknown_endpoint_rejected(self):
        with self.assertRaisesRegex(ValueError, 'end version'):
            choose_runs([run('v0.33.0', 33), run('v0.34.0', 34)], end_version='v0.35.0')

    def test_invalid_range_rejected(self):
        with self.assertRaisesRegex(ValueError, 'lower than'):
            choose_runs([run('v0.33.0', 33), run('v0.34.0', 34)], 'v0.34.0', 'v0.33.0')

    def test_improved_regressed_and_neutral(self):
        runs = [run('v0.32.0', 32, 100), run('v0.33.0', 33, 110),
                run('v0.34.0', 34, 100)]
        self.assertEqual([c for _, c in metric_cells(runs, 'example', 'output_throughput', 1)],
                         ['neutral', 'green', 'red'])
        self.assertEqual([c for _, c in metric_cells(runs, 'example', 'p99_ttft_ms', -1)],
                         ['neutral', 'green', 'red'])
        runs[1]['summary']['models'][0]['metrics']['output_throughput'] = 102
        self.assertEqual(metric_cells(runs, 'example', 'output_throughput', 1)[1][1], 'neutral')

    def test_missing_measurement_never_colored(self):
        runs = [run('v0.32.0', 32, 100), run('v0.33.0', 33, 110, 'FAIL'),
                run('v0.34.0', 34, 130)]
        self.assertEqual([c for _, c in metric_cells(runs, 'example', 'output_throughput', 1)],
                         ['neutral', 'neutral', 'neutral'])

    def test_report_has_multi_release_columns_and_colored_email(self):
        runs = [run('v0.32.0', 32, 100), run('v0.33.0', 33, 110),
                run('v0.34.0', 34, 95)]
        with tempfile.TemporaryDirectory() as td:
            generate_range(runs, 'org/repo', Path(td))
            md = (Path(td) / 'latest.md').read_text()
            notification = json.loads((Path(td) / 'notification.json').read_text())
            email = notification['email_html']
            self.assertIn('| Model | v0.32.0 | v0.33.0 | v0.34.0 |', md)
            self.assertIn('🟢 110.00', md)
            self.assertIn('🔴 95.00', md)
            self.assertNotIn('| Delta |', md)
            self.assertNotIn('| Assessment |', md)
            self.assertIn('color:#16733a', email)
            self.assertIn('color:#b42318', email)
            self.assertLess(email.index('<h2>'), email.index('Validation scope:'))


if __name__ == '__main__':
    unittest.main()
