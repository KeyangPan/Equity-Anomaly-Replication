"""Download Sharadar tables once, then read them from local parquet files.

Usage:
    uv run python -m anomaly_replication.loader      # download everything
    prices = load_table("stocks")                    # read one table in a notebook
"""

import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.csv
import pyarrow.parquet
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
API_URL = "https://api.sharadar.com/v1.0/data"
FRENCH_FACTORS_URL = (
    "http://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
)

# The Sharadar tables this project uses.
TABLES = {
    "stocks": "daily open/high/low/close/volume and adjusted close, one row per ticker per day",
    "tickers": "one row per security: category, exchange, delisted flag, first/last price date",
    "daily": "daily market cap and valuation ratios, one row per ticker per day",
    "fundamentals": "quarterly and annual financial statements (EPS for PEAD)",
    "events": "8-K filing dates and event codes (earnings announcement dates for PEAD)",
    "actions": "splits, dividends, delistings, mergers, ticker changes",
}


def download_table(table: str) -> Path:
    """Download the full history of one table and save it as data/<table>.parquet."""
    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.environ["SHARADAR_API_KEY"]
    DATA_DIR.mkdir(exist_ok=True)
    zip_path = DATA_DIR / f"{table}.csv.zip"
    parquet_path = DATA_DIR / f"{table}.parquet"

    # Step 1: the API answers with a redirect to a temporary download link.
    response = requests.get(
        f"{API_URL}/{table}",
        params={"years": "full"},
        headers={"x-api-key": api_key},
        allow_redirects=False,
        timeout=60,
    )
    if response.status_code != 302:
        raise RuntimeError(f"{table}: expected a redirect, got {response.status_code}: {response.text[:200]}")
    download_url = response.headers["Location"]

    # Step 2: stream the zip file to disk (the key is not sent to the file host).
    with requests.get(download_url, stream=True, timeout=60) as download:
        download.raise_for_status()
        with open(zip_path, "wb") as file:
            for chunk in download.iter_content(chunk_size=1 << 20):
                file.write(chunk)

    # Step 3: read the CSV inside the zip and write it as parquet (smaller, much faster to load).
    with zipfile.ZipFile(zip_path) as archive:
        csv_name = archive.namelist()[0]
        with archive.open(csv_name) as csv_file:
            arrow_table = pyarrow.csv.read_csv(csv_file)
    pyarrow.parquet.write_table(arrow_table, parquet_path)
    zip_path.unlink()

    print(f"{table}: {arrow_table.num_rows:,} rows -> {parquet_path.name}")
    return parquet_path


def load_table(table: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Read data/<table>.parquet into a DataFrame; pass `columns` to read only some columns."""
    parquet_path = DATA_DIR / f"{table}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"{parquet_path} not found; run download_table('{table}') first")
    arrow_table = pyarrow.parquet.read_table(parquet_path, columns=columns)
    # date_as_object=False gives pandas datetime columns instead of slow Python date objects.
    return arrow_table.to_pandas(date_as_object=False)


def download_french_factors() -> Path:
    """Download the daily Fama-French three factors and save them as data/french_factors_daily.parquet.

    Columns: Mkt-RF (market minus risk-free), SMB (small minus big), HML (value minus growth),
    RF (risk-free rate). The file gives percent; we store decimals (0.01 = 1%).
    """
    DATA_DIR.mkdir(exist_ok=True)
    parquet_path = DATA_DIR / "french_factors_daily.parquet"

    response = requests.get(FRENCH_FACTORS_URL, timeout=60)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(archive.namelist()[0]).decode("latin-1")

    # Data lines start with an 8-digit date like 19260701; the other lines are notes.
    data_lines = [line for line in text.splitlines() if line[:8].isdigit()]
    factors = pd.read_csv(io.StringIO("\n".join(data_lines)), names=["date", "Mkt-RF", "SMB", "HML", "RF"])
    factors["date"] = pd.to_datetime(factors["date"].astype(str), format="%Y%m%d")
    factors = factors.set_index("date") / 100

    factors.to_parquet(parquet_path)
    print(f"french_factors_daily: {len(factors):,} rows -> {parquet_path.name}")
    return parquet_path


if __name__ == "__main__":
    for table_name in TABLES:
        download_table(table_name)
    download_french_factors()
