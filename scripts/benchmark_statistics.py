"""Aggregate independent benchmark repetitions without pooling percentiles."""
import math
import statistics


def aggregate_runs(runs):
    if not runs:
        raise ValueError("No benchmark runs")
    metrics, stats = {}, {}
    for key in sorted(set().union(*(r.keys() for r in runs))):
        values = [r.get(key) for r in runs]
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool)
                   and math.isfinite(v) for v in values):
            continue
        mean = statistics.mean(values)
        sd = statistics.stdev(values) if len(values) > 1 else None
        cv = 100 * sd / abs(mean) if sd is not None and mean else None
        if key in ("completed", "failed"):
            metrics[key] = sum(values)
        elif key.startswith(("p99_", "median_")):
            metrics[key] = statistics.median(values)
        else:
            metrics[key] = mean
        stats[key] = {"n": len(values), "mean": mean, "stddev": sd,
                      "cv_pct": cv, "min": min(values), "max": max(values),
                      "median": statistics.median(values),
                      "variability": ("unknown" if cv is None else "stable" if cv <= 2
                                      else "warning" if cv <= 5 else "high" if cv <= 10
                                      else "investigate")}
    return metrics, stats


def format_metric(row, key):
    value = (row.get("metrics") or {}).get(key)
    if value is None:
        return "-"
    stat = (row.get("benchmark_statistics") or {}).get(key, {})
    sd = stat.get("stddev")
    if sd is None:
        return f"{value:.2f}" if isinstance(value, (int, float)) else str(value)
    cv = stat.get("cv_pct")
    suffix = f"; CV {cv:.1f}% {stat['variability']}" if cv is not None else ""
    return f"{value:.2f} ± {sd:.2f} (n={stat['n']}{suffix})"


def variability_rows(runs, metrics):
    for run in runs:
        version = run["metadata"]["version"]
        for row in run["summary"]["models"]:
            if row.get("status") != "PASS":
                continue
            if not row.get("benchmark_statistics"):
                continue
            yield [version, row["model"], *[format_metric(row, key) for key in metrics]]
