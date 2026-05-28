import pandas as pd
df = pd.read_csv("../NFLProjectionModel/data/raw/depth_charts/depth_charts_2025.csv", low_memory=False)
df["dt_parsed"] = pd.to_datetime(df["dt"], errors="coerce", utc=True).dt.tz_localize(None)
season_start = pd.Timestamp(2025, 9, 1)
reg = df[df["dt_parsed"] >= season_start]
dates = sorted(reg["dt_parsed"].dropna().unique())
print(f"Total rows: {len(df)}")
print(f"Rows after Sept 1: {len(reg)}")
print(f"Unique post-Sept-1 dates: {len(dates)}")
print("First 25 dates:")
for d in dates[:25]:
    print(" ", pd.Timestamp(d).date())
