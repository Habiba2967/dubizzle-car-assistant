"""
prepare_data.py
----------------
Turns the assessment's raw Excel dataset into a single clean cars.csv
that the FastAPI backend loads at startup.

Run once:
    uv run python data/prepare_data.py
"""

import argparse
import hashlib
import random
import re
from pathlib import Path

import pandas as pd

MILEAGE_RE = re.compile(r"([\d,]{3,7})\s*(?:km|kms)", re.IGNORECASE)

def extract_mileage(description: str) -> int | None:
    if not isinstance(description, str):
        return None
    m = MILEAGE_RE.search(description)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def main(xlsx_path: str, out_path: str):
    xls = pd.ExcelFile(xlsx_path)
    df = pd.read_excel(xls, sheet_name="cleaned dataset")

    df = df.rename(columns={"Listing_ID": "listing_id"})
    df["listing_id"] = df["listing_id"].astype(int)
    df["year"] = df["year"].astype(int)
    df["make"] = df["make"].astype(str).str.strip()
    df["model"] = df["model"].astype(str).str.strip()
    df["trim"] = df["trim"].astype(str).str.strip()

    df["mileage_km"] = df["description"].apply(extract_mileage)

    cols = [
        "listing_id", "year", "make", "model", "trim", "title",
        "mileage_km", "description", "photo_url",
    ]
    df = df[cols]
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} listings to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", default="data/raw_sample_cars_dataset.xlsx")
    parser.add_argument("--out", default="data/cars.csv")
    args = parser.parse_args()
    main(args.xlsx, args.out)
