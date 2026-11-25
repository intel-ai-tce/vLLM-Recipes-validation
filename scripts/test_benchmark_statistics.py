import unittest
from benchmark_statistics import aggregate_runs, format_metric


class BenchmarkStatisticsTests(unittest.TestCase):
    def test_repetitions(self):
        metrics, stats = aggregate_runs([{"output_throughput": v, "p99_ttft_ms": t,
                                        "completed": 100} for v, t in [(90, 10), (100, 20), (110, 90)]])
        self.assertEqual(metrics["output_throughput"], 100)
        self.assertEqual(stats["output_throughput"]["stddev"], 10)
        self.assertEqual(stats["output_throughput"]["cv_pct"], 10)
        self.assertEqual(metrics["p99_ttft_ms"], 20)
        self.assertEqual(metrics["completed"], 300)
        self.assertIn("± 10.00", format_metric({"metrics": metrics, "benchmark_statistics": stats}, "output_throughput"))

    def test_single_and_missing(self):
        metrics, stats = aggregate_runs([{"output_throughput": 10}])
        self.assertIsNone(stats["output_throughput"]["stddev"])
        self.assertEqual(format_metric({"metrics": metrics}, "output_throughput"), "10.00")
        self.assertNotIn("output_throughput", aggregate_runs([metrics, {}])[0])
