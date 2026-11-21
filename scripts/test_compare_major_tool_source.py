#!/usr/bin/env python3
"""Additional coverage for opt-in cross-tool-source major comparisons."""
import json
import tempfile
import unittest
from pathlib import Path

from compare_major import compatible, generate, select_pair


def item(version, number):
    return {
        "metadata": {
            "version": version, "runner": "local", "bench_num_prompts": "100",
            "bench_num_warmups": "3", "model": "", "image_override": "",
            "recipe_api_base": "https://recipes.vllm.ai", "vllm_env_vars": "",
            "recipe_tools_repo": "https://github.com/vllm-project/vllm.git"
            if version == "v0.33.0" else "", "recipe_tools_ref": "main",
        },
        "summary": {
            "run_id": f"major-{number}", "run_date": "2026-11-20",
            "tested": 1, "passed": 1, "failed": 0,
            "recipe_tools": {"source": "github" if version == "v0.33.0" else "image"},
            "models": [{
                "model": "example", "status": "PASS",
                "workload": {"num_prompts": 100, "num_warmups": 3,
                             "input_tokens": 128, "output_tokens": 128},
                "benchmark_profile": {"dataset_name": "random", "endpoint": "/v1/completions"},
                "benchmark_trust_remote_code": False,
                "metrics": {"output_throughput": 100 + number,
                            "p99_ttft_ms": 100, "p99_tpot_ms": 100},
            }],
        },
        "run_id": str(number), "url": f"https://github.com/org/repo/actions/runs/{number}",
    }


class ToolSourceComparisonTests(unittest.TestCase):
    def test_explicit_opt_in_and_workload_equivalence(self):
        old, new = item("v0.33.0", 19), item("v0.34.0", 20)
        with self.assertRaisesRegex(ValueError, "No compatible lower-release"):
            select_pair([new, old])
        baseline, candidate = select_pair([new, old], allow_tool_source_change=True)
        self.assertEqual((baseline["run_id"], candidate["run_id"]), ("19", "20"))
        new["summary"]["models"][0]["workload"]["output_tokens"] = 256
        self.assertFalse(compatible(old, new, allow_tool_source_change=True))
        new["summary"]["models"][0]["workload"]["output_tokens"] = 128
        new["summary"]["models"][0]["benchmark_profile"]["endpoint"] = "/different"
        self.assertFalse(compatible(old, new, allow_tool_source_change=True))
        new["summary"]["models"][0]["benchmark_profile"]["endpoint"] = "/v1/completions"
        new["metadata"]["bench_num_warmups"] = "0"
        self.assertFalse(compatible(old, new, allow_tool_source_change=True))

    def test_requires_auditable_shared_pass(self):
        old, new = item("v0.33.0", 19), item("v0.34.0", 20)
        old["summary"]["models"][0].pop("workload")
        self.assertFalse(compatible(old, new, allow_tool_source_change=True))
        old["summary"]["models"][0]["workload"] = new["summary"]["models"][0]["workload"].copy()
        old["summary"]["models"][0]["status"] = "FAIL"
        self.assertFalse(compatible(old, new, allow_tool_source_change=True))

    def test_warning_and_coverage_in_markdown_and_email(self):
        old, new = item("v0.33.0", 19), item("v0.34.0", 20)
        with tempfile.TemporaryDirectory() as tmp:
            generate(old, new, "org/repo", Path(tmp))
            md = (Path(tmp) / "latest.md").read_text()
            email = json.loads((Path(tmp) / "notification.json").read_text())["email_html"]
            self.assertIn("tool implementation equivalence is NOT established", md)
            self.assertIn("Benchmark tooling caveat", email)
            self.assertIn("Models passing both releases: 1", md)


if __name__ == "__main__":
    unittest.main()
