"""Real dirty datasets with known clean versions: Hospital, Flights and Beers.

These are the standard cell-level error-detection and repair datasets used by Raha and
Baran (Mahdavi et al., SIGMOD 2019 and VLDB 2020). They are downloaded on first use from
the Raha repository at a pinned commit, not redistributed here. Raha is Apache-2.0.
"""
from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

RAHA_COMMIT = "7be1334b8c7bbdac3f47ef514fb3e1e8c5fc181c"
BASE = f"https://raw.githubusercontent.com/BigDaMa/raha/{RAHA_COMMIT}/datasets"
DATASETS = ("hospital", "flights", "beers")


def cache_dir() -> Path:
    return Path(os.environ.get("DATARANGER_CACHE", Path.home() / ".cache" / "dataranger")) / "realdata"


def fetch(name: str, dest: Path | None = None) -> Path:
    if name not in DATASETS:
        raise ValueError(f"unknown dataset {name!r}; choose from {DATASETS}")
    d = (dest or cache_dir()) / name
    d.mkdir(parents=True, exist_ok=True)
    for part in ("clean", "dirty"):
        f = d / f"{part}.csv"
        if not f.exists():
            urllib.request.urlretrieve(f"{BASE}/{name}/{part}.csv", f)
    return d


@dataclass
class RealCase:
    name: str
    clean: pd.DataFrame
    dirty: pd.DataFrame
    errors: set[tuple[int, str]]  # (row position, column) of every cell that differs

    def summary(self) -> dict:
        cells = self.dirty.shape[0] * self.dirty.shape[1]
        cols = sorted({c for _, c in self.errors})
        return {"dataset": self.name, "rows": self.dirty.shape[0], "columns": self.dirty.shape[1],
                "error_cells": len(self.errors), "error_rate": round(len(self.errors) / cells, 4),
                "columns_with_errors": cols}


def load(name: str, path: Path | None = None) -> RealCase:
    d = path or fetch(name)
    read = lambda p: pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8")
    clean, dirty = read(d / "clean.csv"), read(d / "dirty.csv")
    if clean.shape != dirty.shape:
        raise ValueError(f"{name}: clean {clean.shape} and dirty {dirty.shape} differ in shape")
    clean.columns = dirty.columns  # headers differ in spelling only; columns align by position
    diff = clean.to_numpy() != dirty.to_numpy()
    errors = {(int(r), dirty.columns[c]) for r, c in zip(*diff.nonzero())}
    return RealCase(name, clean, dirty, errors)
