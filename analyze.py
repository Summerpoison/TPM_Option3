"""Screening Accuracy Analyzer -- command line entry point.

    python analyze.py                      # text report for every job
    python analyze.py --job saa-seed-21    # one job, by external id
    python analyze.py --json out.json      # machine-readable alongside the text
    python analyze.py --html report.html   # self-contained HTML report
    python analyze.py --candidates         # per-candidate detail, verbatim

Reads PAULSJOB_API_KEY from the environment (or .env). The key is never printed.
"""
from __future__ import annotations

import argparse
import logging
import sys

from analyzer import analysis as analysis_mod
from analyzer import fetch, report
from analyzer.client import ApiError, BlockedError, PaulsjobClient, TransportError
from analyzer.config import Settings
from analyzer.reasons import DEFAULT_CONFIG, BucketConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description="Analyse the AI's candidate screening decisions.",
    )
    parser.add_argument(
        "--job", metavar="EXTERNAL_ID_PREFIX",
        help="only analyse jobs whose external id starts with this (e.g. saa-seed-)",
    )
    parser.add_argument("--json", metavar="PATH", help="also write the full results as JSON")
    parser.add_argument("--html", metavar="PATH",
                        help="also write a self-contained HTML report (no network needed to view)")
    parser.add_argument("--candidates", action="store_true",
                        help="append the per-candidate decision list, verbatim")
    parser.add_argument("--buckets", default=DEFAULT_CONFIG,
                        help=f"rejection-reason bucket config (default: {DEFAULT_CONFIG})")
    parser.add_argument("-v", "--verbose", action="store_true", help="log each API request")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(message)s",
    )

    settings = Settings.from_env()
    client = PaulsjobClient(settings.base_url, settings.api_key)

    try:
        dataset = fetch.load(client, external_id_prefix=args.job)
    except BlockedError as exc:
        # Distinguished from an API error on purpose: this one means "fix your
        # client", not "you lack a permission".
        print(f"Could not reach the API.\n  {exc}", file=sys.stderr)
        return 2
    except TransportError as exc:
        print(f"Could not reach the API.\n  {exc}\n"
              f"  Check your network connection and PAULSJOB_BASE_URL.", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"The API rejected the request.\n  {exc}\n"
              f"  Check that your API key is valid and has the job:view permission.",
              file=sys.stderr)
        return 2

    if not dataset.jobs:
        where = f" matching '{args.job}'" if args.job else ""
        print(f"No jobs found{where}. Nothing to analyse.\n"
              f"  Check the --job filter, or seed test data with: python -m seed.seeder",
              file=sys.stderr)
        return 1

    result = analysis_mod.analyse(dataset, BucketConfig.load(args.buckets))

    print(report.render_text(result, dataset))
    if args.candidates:
        print()
        print(report.render_candidates(result, dataset))

    if args.html:
        from analyzer.html_report import render_html
        with open(args.html, "w", encoding="utf-8") as handle:
            handle.write(render_html(result, dataset, report.funnel_notes(result)))
        print(f"\nHTML written to {args.html}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(report.render_json(result, dataset))
        print(f"\nJSON written to {args.json}")

    if not dataset.records:
        print("\nNo AI decisions were found on these jobs, so there is nothing to measure.\n"
              "  Candidates may not have reached a step with a screening agent yet.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
