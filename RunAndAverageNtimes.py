"""Run REMM multiple times and average the resulting SEData outputs."""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

# Update this value to change the default number of REMM runs.
N_RUNS = 6

REPO_ROOT = Path(__file__).resolve().parent
RUN_SCRIPT = REPO_ROOT / "RunREMM.py"
REMM_DIR = (
    REPO_ROOT
    / "TDM"
    / "_TDMv9.0.0_REMM"
    / "1_Inputs"
    / "2_SEData"
    / "REMM"
)
SE_PARENT_DIR = REMM_DIR.parent
ID_COLUMNS = [";TAZID", "CO_NAME"]
DEFAULT_START_YEAR = 2023
DEFAULT_END_YEAR = 2054


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Run REMM N times, snapshot each REMM SEData folder, and build "
            "averaged SE_YYYY.csv outputs."
        )
    )
    parser.add_argument(
        "runs",
        nargs="?",
        type=int,
        default=N_RUNS,
        help=f"Number of REMM runs to execute. Defaults to N_RUNS={N_RUNS}.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=DEFAULT_START_YEAR,
        help=f"First SE year to average. Default: {DEFAULT_START_YEAR}.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=DEFAULT_END_YEAR,
        help=f"Last SE year to average. Default: {DEFAULT_END_YEAR}.",
    )
    parser.add_argument(
        "--skip-runs",
        action="store_true",
        help="Do not run RunREMM.py; average existing snapshot folders instead.",
    )
    parser.add_argument(
        "--snapshot",
        action="append",
        type=Path,
        default=[],
        help=(
            "Existing REMM snapshot folder to include. Use once per folder. "
            "Required with --skip-runs."
        ),
    )
    return parser.parse_args(argv)


def unique_path(base_path: Path) -> Path:
    """Return base_path, or a numbered variant if base_path already exists."""
    if not base_path.exists():
        return base_path

    counter = 2
    while True:
        candidate = base_path.with_name(f"{base_path.name}_{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def make_stamp() -> str:
    """Return the timestamp used in snapshot and average folder names."""
    return datetime.now().strftime("%d%m%Y_%H")


def run_remm(run_number: int, total_runs: int) -> None:
    """Run RunREMM.py once and stream its output to the console."""
    print(f"\n=== Starting REMM run {run_number} of {total_runs} ===", flush=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        [sys.executable, "-u", str(RUN_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in process.stdout:
        print(line, end="", flush=True)

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, process.args)

    print(f"=== Finished REMM run {run_number} of {total_runs} ===", flush=True)


def copy_snapshot(stamp: str, run_number: int) -> Path:
    """Copy the current REMM folder to a run-specific snapshot folder."""
    destination = unique_path(SE_PARENT_DIR / f"REMM_{stamp}_run{run_number:03d}")
    print(f"Copying snapshot to {destination}", flush=True)
    shutil.copytree(REMM_DIR, destination)
    return destination


def read_run_year_csv(snapshot_dir: Path, year: int) -> pd.DataFrame:
    """Read one SE year file from a snapshot folder and validate ID columns."""
    csv_path = snapshot_dir / f"SE_{year}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing required file: {csv_path}")

    data = pd.read_csv(csv_path)
    missing_ids = [column for column in ID_COLUMNS if column not in data.columns]
    if missing_ids:
        missing_columns = ", ".join(missing_ids)
        raise ValueError(f"{csv_path} is missing ID columns: {missing_columns}")

    return data


def average_year(snapshot_dirs: Sequence[Path], year: int) -> pd.DataFrame:
    """Average all numeric SE columns for one year across snapshot folders."""
    first = read_run_year_csv(snapshot_dirs[0], year)
    output_columns = list(first.columns)
    value_columns = [column for column in output_columns if column not in ID_COLUMNS]

    first_indexed = first.set_index(ID_COLUMNS, drop=False)
    expected_keys = first_indexed.index
    summed_values = None

    for snapshot_dir in snapshot_dirs:
        data = read_run_year_csv(snapshot_dir, year)
        indexed = data.set_index(ID_COLUMNS, drop=False)

        if not indexed.index.equals(expected_keys):
            raise ValueError(
                f"ID rows in {snapshot_dir / f'SE_{year}.csv'} do not match "
                "the first run. Averaging requires matching ;TAZID and CO_NAME rows."
            )

        numeric_values = indexed[value_columns].apply(pd.to_numeric, errors="raise")
        summed_values = (
            numeric_values
            if summed_values is None
            else summed_values.add(numeric_values, fill_value=0)
        )

    averaged_values = summed_values / len(snapshot_dirs)
    averaged = first_indexed[ID_COLUMNS].join(averaged_values)
    return averaged[output_columns].reset_index(drop=True)


def build_average_folder(
    snapshot_dirs: Sequence[Path],
    runs: int,
    stamp: str,
    start_year: int,
    end_year: int,
) -> Path:
    """Create the averaged REMM output folder."""
    average_dir = unique_path(SE_PARENT_DIR / f"REMM_averageRUN{runs}_{stamp}")
    print(f"\nCreating average folder: {average_dir}", flush=True)

    # Start from the latest full REMM folder so non-SE support files remain present.
    shutil.copytree(REMM_DIR, average_dir)

    for year in range(start_year, end_year + 1):
        averaged = average_year(snapshot_dirs, year)
        output_path = average_dir / f"SE_{year}.csv"
        averaged.to_csv(output_path, index=False)
        print(f"Wrote averaged {output_path.name}", flush=True)

    return average_dir


def validate_args(args: argparse.Namespace) -> None:
    """Validate command-line arguments and required input paths."""
    if args.runs < 1:
        raise ValueError("runs must be 1 or greater.")
    if args.start_year > args.end_year:
        raise ValueError("--start-year must be less than or equal to --end-year.")
    if not RUN_SCRIPT.exists():
        raise FileNotFoundError(f"Could not find {RUN_SCRIPT}")
    if not REMM_DIR.exists():
        raise FileNotFoundError(f"Could not find {REMM_DIR}")
    if args.skip_runs and len(args.snapshot) != args.runs:
        raise ValueError("--skip-runs requires exactly one --snapshot folder per run.")


def collect_snapshot_dirs(args: argparse.Namespace, stamp: str) -> list[Path]:
    """Run REMM and capture snapshots, or resolve provided snapshot folders."""
    if args.skip_runs:
        return [path.resolve() for path in args.snapshot]

    snapshot_dirs = []
    for run_number in range(1, args.runs + 1):
        run_remm(run_number, args.runs)
        snapshot_dirs.append(copy_snapshot(stamp, run_number))
    return snapshot_dirs


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Run the REMM averaging workflow."""
    args = parse_args(argv)
    validate_args(args)

    stamp = make_stamp()
    snapshot_dirs = collect_snapshot_dirs(args, stamp)

    average_dir = build_average_folder(
        snapshot_dirs,
        args.runs,
        stamp,
        args.start_year,
        args.end_year,
    )
    print("\nCompleted REMM averaging.")
    print("Snapshots:")
    for snapshot_dir in snapshot_dirs:
        print(f"  {snapshot_dir}")
    print(f"Average folder:\n  {average_dir}")


if __name__ == "__main__":
    main()
