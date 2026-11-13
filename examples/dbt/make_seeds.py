"""Write DataRanger's built-in demo data (with planted problems) as dbt seed files."""
from datetime import date, timedelta
from pathlib import Path

from dataranger.bench import generate, inject


def main():
    seeds = Path(__file__).parent / "seeds"
    seeds.mkdir(exist_ok=True)
    case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
    case.warehouse["transactions"].to_csv(seeds / "raw_transactions.csv", index=False)
    case.warehouse["customers"].to_csv(seeds / "raw_customers.csv", index=False)
    case.manifest.to_csv(seeds / "raw_control_totals.csv", index=False)
    print(f"Wrote 3 seed files to {seeds}")


if __name__ == "__main__":
    main()
