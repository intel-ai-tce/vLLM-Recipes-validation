#!/usr/bin/env python3
"""Regression checks for major release pairing and report rendering."""
import json
import tempfile
import unittest
from pathlib import Path

from compare_major import eligible, generate, release, select_pair


def item(version, number, runner="local", model="", image_override=""):
    return {"metadata": {"version": version, "runner": runner,
                         "bench_num_prompts": "100", "model": model,
                         "image_override": image_override},
            "summary": {"run_id": f"major-{number}", "run_date": "2026-10-24",
                        "tested": 1, "passed": 1, "failed": 0,
                        "models": [{"model": "example", "status": "PASS",
                                    "metrics": {"output_throughput": 100 + number,
                                                "p99_ttft_ms": 100,
                                                "p99_tpot_ms": 100}}]},
            "run_id": str(number), "url": f"https://github.com/org/repo/actions/runs/{number}"}


class MajorReportTests(unittest.TestCase):
    def test_semantic_version_order_not_lexical(self):
        self.assertGreater(release("v0.31.0"), release("v0.9.0"))

    def test_baseline_lower_regardless_of_chronology(self):
        baseline, candidate = select_pair([item("v0.30.0", 3), item("v0.31.0", 2)])
        self.assertEqual(baseline["metadata"]["version"], "v0.30.0")
        self.assertEqual(candidate["metadata"]["version"], "v0.31.0")

    def test_skip_repeat_and_incompatible(self):
        baseline, candidate = select_pair([item("v0.31.0", 5), item("v0.31.0", 4),
                                           item("v0.30.0", 3, runner="aws"),
                                           item("v0.30.0", 2)])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("2", "5"))

    def test_exclude_partial_and_custom_images(self):
        self.assertFalse(eligible(item("v0.31.0", 1, model="example")["metadata"]))
        self.assertFalse(eligible(item("v0.31.0", 1, image_override="test")["metadata"]))

    def test_report_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(item("v0.30.0", 1), item("v0.31.0", 2), "org/repo", Path(tmp))
            md = (Path(tmp) / "latest.md").read_text()
            notice = json.loads((Path(tmp) / "notification.json").read_text())
            self.assertIn("Major Release Comparison: v0.30.0 → v0.31.0", md)
            self.assertIn("Baseline (v0.30.0)", md)
            self.assertEqual(notice["report_type"], "major")
            self.assertIn("<html>", notice["email_html"])
            self.assertIn("/report/major/", notice["exact_report_url"])


if __name__ == "__main__":
    unittest.main()
