#!/usr/bin/env python3
"""Regression tests for archived vLLM benchmark commands."""
import shlex
import unittest

from run_one_model import benchmark_shell_command


class BenchmarkCommandTests(unittest.TestCase):
    def test_shell_command_round_trip(self):
        command = ["docker", "exec", "-e", "HF_HOME=/tmp/test cache", "container",
                   "vllm", "bench", "serve", "--model", "org/Model Name",
                   "--num-warmups", "3", "--num-prompts", "100",
                   "--request-rate", "inf"]
        script = benchmark_shell_command(command)
        self.assertIn("--num-warmups", script)
        self.assertNotIn("HF_HOME=", script)
        self.assertTrue(script.startswith("#!/usr/bin/env bash"))
        cli = "vllm " + script.split("\nvllm ", 1)[1]
        self.assertEqual(shlex.split(cli.replace(chr(92) + "\n", "")), command[5:])


if __name__ == "__main__":
    unittest.main()
