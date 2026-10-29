#!/usr/bin/env python3
"""Regression checks for major release pairing and report rendering."""
import json
import tempfile
import unittest
from pathlib import Path

from compare_major import complete, eligible, generate, hardware, release, select_pair


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
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("3", "5"))

    def test_exclude_partial_and_custom_images(self):
        self.assertFalse(eligible(item("v0.31.0", 1, model="example")["metadata"]))
        self.assertFalse(eligible(item("v0.31.0", 1, image_override="test")["metadata"]))

    def test_latest_same_version_and_failed_workflow_eligible(self):
        recent = item("v0.32.0", 11)
        recent["summary"]["models"][0]["status"] = "FAIL"
        recent["summary"].update(passed=0, failed=1)
        baseline, candidate = select_pair([recent, item("v0.32.0", 9),
                                           item("v0.31.0", 8)])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("8", "11"))

    def test_semver_preferred_over_chronology(self):
        baseline, candidate = select_pair([item("v0.31.0", 12),
                                           item("v0.32.0", 11),
                                           item("v0.30.0", 10)])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("12", "11"))

    def test_latest_compatible_baseline(self):
        baseline, candidate = select_pair([item("v0.31.0", 15),
                                           item("v0.32.0", 14),
                                           item("v0.31.0", 9)])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("15", "14"))

    def test_fallback_to_older_attempt_of_same_version(self):
        newest = item("v0.31.0", 15)
        newest["metadata"]["bench_num_warmups"] = "0"
        candidate = item("v0.32.0", 14)
        candidate["metadata"]["bench_num_warmups"] = "3"
        older = item("v0.31.0", 9)
        older["metadata"]["bench_num_warmups"] = "3"
        baseline, selected = select_pair([newest, candidate, older])
        self.assertEqual((baseline["run_id"], selected["run_id"]), ("9", "14"))

    def test_do_not_fallback_to_older_candidate_release(self):
        newest = item("v0.32.0", 14)
        newest["metadata"]["bench_num_warmups"] = "3"
        with self.assertRaisesRegex(ValueError, "No compatible lower-release"):
            select_pair([newest, item("v0.31.0", 9)])


    def test_partial_run_is_excluded(self):
        partial = item("v0.32.0", 11)
        partial["summary"]["tested"] = 2
        baseline, candidate = select_pair([partial, item("v0.32.0", 9),
                                           item("v0.31.0", 8)])
        self.assertEqual(candidate["run_id"], "9")

    def test_catalog_validation_requires_all_models(self):
        summary = item("v0.32.0", 11)["summary"]
        self.assertTrue(complete(summary, {"models": [{"hf_id": "example"}],
                                           "discovery_errors": []}))
        self.assertFalse(complete(summary, {"models": [{"hf_id": "missing"}],
                                            "discovery_errors": []}))
        self.assertFalse(complete(summary, {"models": [{"hf_id": "example"}],
                                            "discovery_errors": [{"error": "bad"}]}))

    def test_transient_discovery_failure_with_complete_catalog(self):
        summary = item("v0.31.0", 15)["summary"]
        discovery = {"models": [{"hf_id": "example"}],
                     "discovery_errors": [{"hf_id": "missing-recipe",
                                           "error": "Connection reset by peer"}]}
        self.assertTrue(complete(summary, discovery))
        discovery["discovery_errors"][0]["error"] = "Invalid recipe definition"
        self.assertFalse(complete(summary, discovery))
        discovery["discovery_errors"][0]["error"] = "Connection reset by peer"
        discovery["models"][0]["hf_id"] = "different"
        self.assertFalse(complete(summary, discovery))

    def test_parse_hardware(self):
        system = ("===== lscpu =====\nModel name: Xeon ABC\nCPU(s): 288\n"
                  "NUMA node(s): 6\n===== free -h =====\nMem: 512Gi 10Gi\n")
        parsed = hardware(system)
        self.assertEqual(parsed["Model name"], "Xeon ABC")
        self.assertEqual(parsed["Memory"], "512Gi")

    def test_hardware_differences_in_markdown_and_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline, candidate = item("v0.31.0", 8), item("v0.32.0", 11)
            baseline["hardware"] = {"Model name": "Xeon A", "NUMA node(s)": "4"}
            candidate["hardware"] = {"Model name": "Xeon B", "NUMA node(s)": "6"}
            generate(baseline, candidate, "org/repo", Path(tmp))
            md = (Path(tmp) / "latest.md").read_text()
            email = json.loads((Path(tmp) / "notification.json").read_text())["email_html"]
            self.assertIn("⚠️ Different", md)
            self.assertIn("Xeon B", md)
            self.assertIn("Hardware comparison", email)
            self.assertIn("Xeon B", email)

    def test_warmup_policy_must_match(self):
        old = item("v0.31.0", 8)  # Historical run: no field means zero warmups.
        new = item("v0.32.0", 11)
        new["metadata"]["bench_num_warmups"] = "3"
        with self.assertRaisesRegex(ValueError, "compatible workload"):
            select_pair([new, old])
        old["metadata"]["bench_num_warmups"] = "3"
        baseline, candidate = select_pair([new, old])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("8", "11"))

    def test_legacy_warmup_mismatch_is_rejected(self):
        old = item("v0.31.0", 9)
        old["metadata"]["legacy_inferred"] = True
        new = item("v0.32.0", 14)
        new["metadata"]["bench_num_warmups"] = "3"
        with self.assertRaisesRegex(ValueError, "compatible workload"):
            select_pair([new, old])
        old["metadata"]["bench_num_warmups"] = "3"
        baseline, candidate = select_pair([new, old])
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("9", "14"))

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
