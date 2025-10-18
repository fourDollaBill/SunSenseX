import argparse
import os
import json
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import requests
from dateutil import tz


# --- Fixed site coordinates (Sacramento) ---
LAT = 38.58157
LON = -121.49440
TZ = "America/Los_Angeles"


def _horizon_utc(tz_name: str):
    """Return next 24 h in 15-min steps in UTC."""
    tzinfo = tz.gettz(tz_name)
    now = datetime.now(tzinfo)
    minute = (now.minute // 15 + 1) * 15
    if minute >= 60:
        start = (now.replace(minute=0, second=0, microsecond=0)
                 + timedelta(hours=1))
    else:
        start = now.replace(minute=minute, second=0, microsecond=0)
    periods = int(24 * 60 / 15)
    idx_local = pd.date_range(start, periods=periods, freq="15min")
    return idx_local.tz_convert("UTC")


def fetch_open_meteo():
    """Get next ~48h hourly forecast from Open-Meteo (no API key)."""
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ",".join([
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
            "temperature_2m",
            "wind_speed_10m",
            "cloudcover"
        ]),
        "forecast_days": 3,
        "timezone": TZ
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast",
                     params=params, timeout=30)
    r.raise_for_status()
    js = r.json()
    h = js["hourly"]
    df = pd.DataFrame(h)
    df["timestamp"] = pd.to_datetime(df["time"]).dt.tz_localize(
        TZ, nonexistent="shift_forward").dt.tz_convert("UTC")
    df.rename(columns={
        "shortwave_radiation": "ghi",
        "direct_normal_irradiance": "dni",
        "diffuse_radiation": "dhi",
        "temperature_2m": "temp_air",
        "wind_speed_10m": "wind_speed",
        "cloudcover": "cloud_cover"
    }, inplace=True)
    return df[["timestamp", "ghi", "dni", "dhi",
               "temp_air", "wind_speed", "cloud_cover"]]


def fetch_nasa_power():
    """Get hourly data for today+tomorrow from NASA POWER (no key)."""
    tzinfo = tz.gettz(TZ)
    today = datetime.now(tzinfo).date()
    end = today + timedelta(days=2)
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M,WS10M,CLRSKY_SFC_SW_DWN",
        "community": "RE",
        "longitude": LON,
        "latitude": LAT,
        "format": "JSON",
        "start": today.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "temporal": "hourly",
    }
    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    r = requests.get(url, params=params, timeout=45)
    r.raise_for_status()
    js = r.json()["properties"]["parameter"]

    def series(name):
        s = pd.Series(js[name], dtype=float)
        s.index = pd.to_datetime(s.index, format="%Y%m%d%H", utc=True)
        return s

    ghi = series("ALLSKY_SFC_SW_DWN")
    temp = series("T2M")
    wind = series("WS10M")
    clr = series("CLRSKY_SFC_SW_DWN")

    df = pd.DataFrame({"ghi": ghi, "temp_air": temp, "wind_speed": wind})
    df["dni"] = np.nan
    df["dhi"] = np.nan
    if not ghi.empty and not clr.empty:
        ratio = (ghi / clr).clip(upper=1)
        df["cloud_cover"] = (1 - ratio) * 100
    df = df.reset_index().rename(columns={"index": "timestamp"})
    return df


def resample_to_15min(df, idx_utc):
    base = df.set_index("timestamp").sort_index()
    if base.index.tz is None:
        base.index = base.index.tz_localize("UTC")
    base = base.reindex(pd.date_range(
        base.index.min(), base.index.max(), freq="15min", tz="UTC"))
    base = base.ffill()
    out = base.reindex(idx_utc, method="nearest",
                       tolerance=pd.Timedelta("30min"))
    return out.reset_index().rename(columns={"index": "timestamp"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["open-meteo", "nasa-power"],
                        default="open-meteo")
    parser.add_argument("--out", default="data/sacramento.parquet")
    args = parser.parse_args()

    horizon = _horizon_utc(TZ)
    df = fetch_open_meteo() if args.provider == "open-meteo" else fetch_nasa_power()
    df15 = resample_to_15min(df, horizon)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df15.to_parquet(args.out, index=False)
    print(f"Saved {len(df15)} records to {args.out}")


if __name__ == "__main__":
    main()
