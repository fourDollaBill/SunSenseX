# SunSenseX
An app that shows optimal appliance usage time
👥 Role split (3 backend tickets in parallel)
A) Forecasts & Storms (Owner A)

Scope

Implement /forecast (choose pvlib with static fallback)

Implement /storm (returns next storm window + severity)

Timezone handling; 15-min sampling for next 24h

Provide typed Pydantic models for ForecastRow

Files

server/forecast_service.py

server/storm_service.py

server/schemas.py (ForecastRow, StormWindow)

server/config.py (tz, lat/lon, system_kw)

Acceptance

POST /forecast returns 96 rows: [{ts, solar_kw, grid_co2_g_per_kwh}]

GET /storm returns {storm_windows:[{start,end,severity}]} or empty

Works with pvlib and falls back to /data/forecast_*.json if pvlib missing

Tests

tests/test_forecast.py (length 96, monotonic timestamps)

tests/test_storm.py (window within next 24–48h)

B) Recommend & Learning (Owner B)

Scope

Deterministic scorer + window search

Contextual bandit (Thompson Sampling) for top-k time choices

/recommend (POST) and /accept (POST) endpoints

Savings & CO₂ math

Files

server/recommender.py (scorer + windowing)

server/bandit.py (state in data/bandit_state.json)

server/routes_recommend.py

Update server/schemas.py with Appliance, Tariff, UserCtx, Nudge

Acceptance

POST /recommend returns 2–5 ranked nudges with:

{appliance, suggested_start, window, reason,
 est_savings_kwh, est_savings_usd, est_co2_kg, confidence}


POST /accept updates bandit with reward (1 on accept, 0 on ignore)

Repeated accepts nudge the next recommendations (confidence shifts)

Tests

tests/test_recommend.py (no overlaps, respects quiet hours)

tests/test_bandit.py (alpha/beta update; action selection not constant)

C) Coach & Impact (Owner C)

Scope

/coach short message generator (template or LLM call if allowed)

/impact returns cumulative totals for kWh/$/CO₂ and EcoScore

Logging of actions to data/impact_log.json

Files

server/coach.py (rule-based text with placeholders)

server/impact.py (accumulates totals)

server/routes_misc.py

Acceptance

POST /coach with {nudge, eco_score} → returns friendly one-liner

GET /impact → {kwh_total, usd_total, co2_total, eco_score}

Log file appends on each /accept

Tests

tests/test_coach.py (non-empty, references numbers)

tests/test_impact.py (totals increment after accept)

🗂️ Suggested backend layout
server/
  main.py
  config.py
  schemas.py
  forecast_service.py
  storm_service.py
  recommender.py
  bandit.py
  coach.py
  impact.py
  routes_recommend.py
  routes_misc.py
data/
  forecast_sunny.json
  forecast_cloudy.json
  forecast_storm.json
  tariff_default.json
  appliances_default.json
  user_ctx_default.json
  weather_storm.json
  bandit_state.json
  impact_log.json
tests/
  test_forecast.py
  test_recommend.py
  test_bandit.py
  test_coach.py
  test_impact.py

🔌 API contracts (exact)

POST /forecast body: { lat, lon, tz, system_kw, scenario } → [{ts, solar_kw, grid_co2_g_per_kwh}]

GET /storm → {storm_windows:[{start,end,severity}]}

GET /tariff → tariff JSON

GET /appliances → appliances JSON

GET /userctx → user context JSON

POST /recommend body:

{ forecast: [...], appliances: [...], tariff: {...}, user_ctx: {...} }


→ [Nudge]

POST /accept body: {context_key, action, accepted: true|false, nudge} → {ok:true}

POST /coach body: {nudge, eco_score} → {message}

GET /impact → totals

Keep payloads small and consistent; freeze them early.

⏱️ Mini-timeline (backend only)

H 0–3

A: /forecast stub (static JSON) + schemas

B: /recommend stub (returns mock nudges)

C: /coach stub + /impact counters

H 3–8

A: pvlib path + fallback; /storm

B: real scorer + windowing; savings math; /accept logs

C: coach templates; impact log rolling up

H 8–12

Integrate with iOS; fix CORS; small unit tests pass

Lock contracts; write curl examples in README

H 12–20

B: bandit (Thompson) + persistence

A: params from config.py; timezone correctness

C: polish messages; totals endpoint

H 20–24

Bug bash, guardrails, error messages

Freeze interfaces

✅ Definition of Done per module

Forecast/Storm

 96 rows / 15-min cadence

 tz correct; UTC in payload

 scenario scale works (sunny/cloudy/storm)

 storm window triggers within 12h

Recommend/Learn

 No quiet-hour violations

 On-peak penalty respected

 Top suggestion reproducible (same inputs → same output)

 Bandit state file grows after accepts

Coach/Impact

 Message under 120 chars, includes a number

 Impact totals match number of accepts

 Log file append-only; no crashes on empty file

🧪 Test & tooling quickies

Add make dev (or a run.ps1) to start Uvicorn with reload.

Add pytest -q for fast tests.

Add /health endpoint.

Curl pack (drop into README):

curl -X POST :8001/forecast -H "Content-Type: application/json" -d "{\"lat\":37.77,\"lon\":-122.42,\"tz\":\"America/Los_Angeles\",\"system_kw\":4,\"scenario\":\"sunny\"}"
curl :8001/storm
curl -X POST :8001/recommend -H "Content-Type: application/json" -d @data/sample_payload.json
curl -X POST :8001/accept -H "Content-Type: application/json" -d "{\"context_key\":\"Washer_15_sunny\",\"action\":\"15:10\",\"accepted\":true}"
curl :8001/impact

🧯 Risk & fallback

pvlib issues on Windows → keep static JSON scenarios; flip later.

Bandit not stabilizing → default to rule-based ranking; log “learning planned.”

Time zone bugs → convert all ts to UTC in API; only display local in app.
