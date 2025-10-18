# server/recommender.py
import json, os
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any

DATA_DIR = "data"
STEP_MIN = 15

# ---------- helpers
def hms_to_time(s: str) -> time:
    return datetime.strptime(s, "%H:%M").time()

def time_to_hhmm(t: time) -> str:
    return t.strftime("%H:%M")

def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    # assumes same-day ranges
    return (a_start < b_end) and (b_start < a_end)

def minutes_between(a: time, b: time) -> int:
    dt_a = datetime.combine(date.today(), a)
    dt_b = datetime.combine(date.today(), b)
    return int((dt_b - dt_a).total_seconds() // 60)

def iter_starts(win_start: time, win_end: time, duration_min: int):
    cur = datetime.combine(date.today(), win_start)
    end = datetime.combine(date.today(), win_end)
    while cur + timedelta(minutes=duration_min) <= end:
        yield cur.time()
        cur += timedelta(minutes=STEP_MIN)

# ---------- load data
def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_tariff() -> Dict[str, Any]:
    return load_json(os.path.join(DATA_DIR, "tariff_default.json"))

def load_appliances() -> List[Dict[str, Any]]:
    return load_json(os.path.join(DATA_DIR, "appliances_default.json"))

def try_load_forecast() -> List[Dict[str, Any]]:
    # optional: load forecast_sunny.json if you have it
    path = os.path.join(DATA_DIR, "forecast_sunny.json")
    if os.path.exists(path):
        return load_json(path)
    return []

# ---------- synthesize a simple sunny forecast if missing
def synth_sunny_forecast() -> List[Dict[str, Any]]:
    # 24h at 15-min steps (96 points) with a bell-ish curve peaking ~13:00
    points = []
    start = datetime.combine(date.today(), time(0, 0))
    for i in range(96):
        t = start + timedelta(minutes=STEP_MIN * i)
        hour = t.hour + t.minute/60
        # simple solar shape: 0 at night, peak ~1.0 at 13:00
        if 6 <= hour <= 19:
            # normalized bump using a cosine
            x = (hour - 6) / (19 - 6)  # 0..1 day window
            solar_norm = max(0.0, (1 - (2*abs(x - 0.5))**1.8))  # rounded peak
        else:
            solar_norm = 0.0
        points.append({
            "ts_local": t.strftime("%H:%M"),
            "solar_kw": round(3.0 * solar_norm, 3),  # pretend 3 kW peak system
            "grid_co2_g_per_kwh": 520 if 16 <= hour <= 21 else 380  # dirtier in peak
        })
    return points

# ---------- tariff helpers
def effective_rate_for_window(start: time, end: time, tariff: Dict[str, Any]) -> float:
    on_peak_blocks = tariff.get("on_peak", [])
    on_peak_rate = float(tariff["on_peak_rate"])
    off_peak_rate = float(tariff["off_peak_rate"])
    # if ANY minute of the window touches on-peak, charge on-peak
    for blk in on_peak_blocks:
        b_start = hms_to_time(blk["start"])
        b_end   = hms_to_time(blk["end"])
        if overlaps(start, end, b_start, b_end):
            return on_peak_rate
    return off_peak_rate

def solar_kwh_in_window(start: time, end: time, forecast: List[Dict[str, Any]]) -> float:
    # sum solar over STEP_MIN slices, convert kW * hours
    cur = datetime.combine(date.today(), start)
    end_dt = datetime.combine(date.today(), end)
    total_kwh = 0.0
    # build quick lookup by HH:MM
    by_hhmm = {row["ts_local"]: float(row["solar_kw"]) for row in forecast}
    while cur < end_dt:
        nxt = cur + timedelta(minutes=STEP_MIN)
        hhmm = cur.strftime("%H:%M")
        solar_kw = by_hhmm.get(hhmm, 0.0)
        total_kwh += solar_kw * (STEP_MIN / 60.0)
        cur = nxt
    return total_kwh

def avg_co2_in_window(start: time, end: time, forecast: List[Dict[str, Any]]) -> float:
    cur = datetime.combine(date.today(), start)
    end_dt = datetime.combine(date.today(), end)
    vals = []
    by_hhmm = {row["ts_local"]: int(row["grid_co2_g_per_kwh"]) for row in forecast}
    while cur < end_dt:
        hhmm = cur.strftime("%H:%M")
        vals.append(by_hhmm.get(hhmm, 400))
        cur += timedelta(minutes=STEP_MIN)
    if not vals:
        return 400.0
    return sum(vals) / len(vals)  # g/kWh

# ---------- core scoring (cost-first + solar credit)
def score_start(appliance: Dict[str, Any], start: time, tariff: Dict[str, Any],
                forecast: List[Dict[str, Any]]) -> Dict[str, Any]:
    duration = int(appliance["duration_min"])
    end = (datetime.combine(date.today(), start) + timedelta(minutes=duration)).time()
    kwh = float(appliance["kwh"])

    eff_rate = effective_rate_for_window(start, end, tariff)  # $/kWh
    cost = eff_rate * kwh

    # solar credit: how much solar available during the window (kWh),
    # capped by appliance kWh to avoid over-crediting
    solar_kwh = min(kwh, solar_kwh_in_window(start, end, forecast))
    # weight for solar credit (tunable)
    lambda_solar = 0.25 * kwh
    rule_score = -cost + lambda_solar * solar_kwh  # higher is better

    # money + co2 deltas for display
    baseline_rate = tariff["on_peak_rate"]  # assume baseline is "if I ran at worst time" (peak)
    delta_usd = max(0.0, (baseline_rate - eff_rate) * kwh)
    co2_old_gpkwh = 520  # typical peak
    co2_new_gpkwh = avg_co2_in_window(start, end, forecast)
    delta_co2_kg = max(0.0, (co2_old_gpkwh - co2_new_gpkwh) * kwh / 1000.0)

    return {
        "start": time_to_hhmm(start),
        "end": time_to_hhmm(end),
        "score": rule_score,
        "effective_rate": eff_rate,
        "baseline_usd": baseline_rate * kwh,
        "suggested_usd": eff_rate * kwh,
        "delta_usd": delta_usd,
        "solar_offset_kwh": solar_kwh,
        "baseline_co2": co2_old_gpkwh * kwh / 1000.0,
        "suggested_co2": co2_new_gpkwh * kwh / 1000.0,
        "on_peak_avoided": eff_rate < tariff["on_peak_rate"],
        "respects_quiet": True  # no user_ctx yet
    }

def best_window_for_appliance(appliance: Dict[str, Any],
                              tariff: Dict[str, Any],
                              forecast: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    # flex_window can be dict or list of dicts
    fw = appliance["flex_window"]
    windows = fw if isinstance(fw, list) else [fw]

    candidates = []
    for win in windows:
        s = hms_to_time(win["start"])
        e = hms_to_time(win["end"])
        for start in iter_starts(s, e, appliance["duration_min"]):
            cand = score_start(appliance, start, tariff, forecast)
            candidates.append(cand)

    if not candidates:
        return None
    top = max(candidates, key=lambda c: c["score"])
    # build nudge payload
    return {
        "appliance": appliance["name"],
        "suggested_start": top["start"],
        "window": f'{top["start"]}-{top["end"]}',
        "reason": "Cheapest window with good solar credit",
        "est_savings_kwh": round(top["solar_offset_kwh"], 2),
        "est_savings_usd": round(top["delta_usd"], 2),
        "est_co2_kg": round(top["baseline_co2"] - top["suggested_co2"], 2),
        "confidence": 0.75,  # static for baseline
        "est_cost_baseline_usd": round(top["baseline_usd"], 2),
        "est_cost_suggested_usd": round(top["suggested_usd"], 2),
        "est_co2_baseline_kg": round(top["baseline_co2"], 2),
        "est_co2_suggested_kg": round(top["suggested_co2"], 2),
        "on_peak_avoided": top["on_peak_avoided"],
        "respects_quiet_hours": top["respects_quiet"],
        "storm_preschedule": False
    }

def recommend_baseline() -> List[Dict[str, Any]]:
    tariff = load_tariff()
    apps = load_appliances()
    fc = try_load_forecast()
    if not fc:
        fc = synth_sunny_forecast()

    nudges = []
    for a in apps:
        n = best_window_for_appliance(a, tariff, fc)
        if n:
            nudges.append(n)

    # global ranking: save cash first, then confidence, then CO2
    nudges.sort(key=lambda n: (n["est_savings_usd"], n["confidence"], n["est_co2_kg"]), reverse=True)
    return nudges

if __name__ == "__main__":
    out = recommend_baseline()
    print(json.dumps(out, indent=2))
