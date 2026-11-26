"""Derive an hourly measured residual from the public UCI 235 archive."""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from io import TextIOWrapper
import json
from pathlib import Path
from zipfile import ZipFile

SOURCE = "https://doi.org/10.24432/C58K54"
ARCHIVE_SHA256 = "9f84b46ade8a2d8e1286ec4b2b6c2987a45a755c59f263be3b3b3d10dfbda3ff"
PROFILE = Path(__file__).with_name("uci_hourly_residual.json")
DAY = "17/12/2006"


def derive(archive):
    with archive.open("rb") as payload:
        actual = sha256(payload.read()).hexdigest()
    if actual != ARCHIVE_SHA256:
        raise ValueError("Input archive checksum differs from the pinned UCI release")
    readings = {}
    clipped = 0
    with ZipFile(archive) as z, z.open("household_power_consumption.txt") as member:
        for row in csv.DictReader(TextIOWrapper(member, encoding="ascii"), delimiter=";"):
            if row["Date"] != DAY:
                if readings:
                    break
                continue
            hour, minute, second = map(int, row["Time"].split(":"))
            if second or not 0 <= hour < 24 or not 0 <= minute < 60 or (hour, minute) in readings:
                raise ValueError("Invalid or duplicate minute timestamp")
            residential_wh = Decimal(row["Global_active_power"]) * 1000 / 60
            metered_wh = sum(Decimal(row[f"Sub_metering_{i}"]) for i in (1, 2, 3))
            residual = residential_wh - metered_wh
            clipped += residual < 0
            readings[hour, minute] = max(Decimal(0), residual)
    if len(readings) != 1440:
        raise ValueError("Selected day is not complete")
    values = [sum(readings[t, m] for m in range(60)) / 100 for t in range(24)]
    return dict(source=SOURCE, archive_sha256=actual, source_date="2006-12-17",
                authors=["Georges Hebrail", "Alice Berard"], license="CC BY 4.0",
                samples=len(readings), clipped_negative_minutes=clipped,
                unit="0.1 kW hourly mean", baseline=[int(v.to_integral_value(rounding=ROUND_HALF_UP)) for v in values])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, default=PROFILE)
    args = parser.parse_args()
    args.output.write_text(json.dumps(derive(args.archive), indent=2) + "\n", encoding="utf-8")
