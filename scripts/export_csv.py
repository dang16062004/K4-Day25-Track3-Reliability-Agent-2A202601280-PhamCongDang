"""Export chaos-run metrics to CSV (evidence for RUBRIC.md's "metrics JSON/CSV" item)."""
from __future__ import annotations

import argparse

from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.csv")
    args = parser.parse_args()
    config = load_config(args.config)
    metrics = run_simulation(config, load_queries())
    metrics.write_csv(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
