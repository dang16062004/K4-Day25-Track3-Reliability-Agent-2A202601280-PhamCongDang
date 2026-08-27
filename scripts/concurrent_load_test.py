"""Concurrent load test.

Runs the gateway under N concurrent worker threads and reports throughput/
latency/availability, so it can be compared against the sequential baseline
in reports/metrics.json. Evidence for RUBRIC.md's "concurrent load" item
under Chaos/load testing.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reliability_lab.chaos import build_gateway, load_queries
from reliability_lab.config import load_config
from reliability_lab.gateway import GatewayResponse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--out", default="reports/metrics_concurrent.json")
    args = parser.parse_args()

    config = load_config(args.config)
    queries = load_queries()
    gateway = build_gateway(config)

    def call_one(_: int) -> tuple[GatewayResponse, float]:
        prompt = random.choice(queries)
        start = time.perf_counter()
        result = gateway.complete(prompt)
        wall_ms = (time.perf_counter() - start) * 1000
        return result, wall_ms

    results: list[tuple[GatewayResponse, float]] = []
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(call_one, i) for i in range(args.requests)]
        for fut in as_completed(futures):
            results.append(fut.result())
    total_wall_ms = (time.perf_counter() - wall_start) * 1000

    total = len(results)
    successful = sum(1 for r, _ in results if r.route != "static_fallback")
    cache_hits = sum(1 for r, _ in results if r.cache_hit)
    static_fallbacks = sum(1 for r, _ in results if r.route == "static_fallback")
    latencies = sorted(w for _, w in results)

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = (len(latencies) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(latencies) - 1)
        return latencies[f] + (latencies[c] - latencies[f]) * (k - f)

    report = {
        "workers": args.workers,
        "total_requests": total,
        "availability": round(successful / total, 4) if total else 0.0,
        "cache_hit_rate": round(cache_hits / total, 4) if total else 0.0,
        "static_fallbacks": static_fallbacks,
        "wall_clock_ms": round(total_wall_ms, 2),
        "throughput_rps": round(total / (total_wall_ms / 1000), 2) if total_wall_ms else 0.0,
        "latency_p50_ms": round(pct(50), 2),
        "latency_p95_ms": round(pct(95), 2),
        "latency_p99_ms": round(pct(99), 2),
        "circuit_open_count": sum(
            1
            for breaker in gateway.breakers.values()
            for entry in breaker.transition_log
            if entry["to"] == "open"
        ),
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
