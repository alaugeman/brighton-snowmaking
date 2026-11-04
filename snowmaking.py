#!/usr/bin/env python3
"""
Wet-bulb snowmaking outlook, in the style of nofmann.github.io/brighton-snowmaking.

Pulls hourly T / RH / surface pressure from Open-Meteo (HRRR + GFS), computes a
pressure-aware wet-bulb, and writes a static, color-banded HTML table (index.html)
that GitHub Pages can host. Run on a schedule (cron or GitHub Actions) to keep it fresh.

    pip install requests
    python snowmaking.py            # writes index.html
"""
import datetime as dt
import math
import requests

# ---- Site config -----------------------------------------------------------
SITE = "Brighton"
LAT, LON = 40.598, -111.583
ELEV_M = 2797                     # ~9,177 ft; forces Open-Meteo's downscaling to this height
TZ = "America/Denver"
THRESHOLD_F = 27                  # "snowmaking-capable" hour

# Bands: (upper wet-bulb °F inclusive, label, color). Tune these to your ops.
BANDS = [
    (20, "Great",      "#1d4ed8"),
    (24, "Good",       "#60a5fa"),
    (27, "Marginal",   "#fde68a"),
    (29, "Borderline", "#fdba74"),
    (999, "Too warm",  "#f87171"),
]

# ---- Wet-bulb (psychrometric, pressure-aware) ------------------------------
def e_sat(t_c):
    """Saturation vapor pressure over water, hPa (Bolton 1980)."""
    return 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))

def wet_bulb_c(t_c, rh, p_hpa):
    """Solve e = e_s(Tw) - γ·P·(T - Tw) for Tw by bisection.
    γ = 6.6e-4·(1 + 0.00115·Tw) /°C (aspirated psychrometer).
    Lower P -> smaller psychrometric term -> colder Tw than a sea-level chart."""
    e = rh / 100.0 * e_sat(t_c)
    lo, hi = t_c - 40.0, t_c
    for _ in range(60):
        tw = 0.5 * (lo + hi)
        f = e_sat(tw) - 6.6e-4 * (1 + 0.00115 * tw) * p_hpa * (t_c - tw) - e
        lo, hi = (tw, hi) if f < 0 else (lo, tw)
    return 0.5 * (lo + hi)

def f2c(f): return (f - 32) / 1.8
def c2f(c): return c * 1.8 + 32

def wet_bulb_f(t_f, rh, p_hpa):
    return c2f(wet_bulb_c(f2c(t_f), rh, p_hpa))

# ---- Data ------------------------------------------------------------------
def fetch(models="gfs_hrrr,gfs_seamless"):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": LAT, "longitude": LON, "elevation": ELEV_M,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m",
        "models": models, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": TZ, "forecast_days": 7,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["hourly"]

def blend(h):
    """HRRR where it exists (~48 h), GFS after. Returns list of dicts."""
    rows = []
    for i, t in enumerate(h["time"]):
        def pick(var):
            v = h.get(f"{var}_gfs_hrrr", [None] * len(h["time"]))[i]
            return (v, "HRRR") if v is not None else (h[f"{var}_gfs_seamless"][i], "GFS")
        (tf, src), (rh, _), (p, _), (ws, _) = (pick(v) for v in
            ("temperature_2m", "relative_humidity_2m", "surface_pressure", "wind_speed_10m"))
        if None in (tf, rh, p):
            continue
        rows.append(dict(time=dt.datetime.fromisoformat(t), t=tf, rh=rh, p=p, wind=ws,
                         src=src, tw=wet_bulb_f(tf, rh, p)))
    return rows

# ---- Render ----------------------------------------------------------------
def band(tw):
    return next(b for b in BANDS if round(tw) <= b[0])

def render(rows):
    days = {}
    for r in rows:
        days.setdefault(r["time"].date(), {})[r["time"].hour] = r
    head = "".join(f"<th>{h:02d}</th>" for h in range(24))
    body = []
    for d, hrs in days.items():
        cells, n = [], 0
        for h in range(24):
            r = hrs.get(h)
            if not r:
                cells.append("<td></td>"); continue
            n += r["tw"] <= THRESHOLD_F
            tip = f'{r["src"]} | T {r["t"]:.0f}°F  RH {r["rh"]:.0f}%  Wind {r["wind"] or 0:.0f} mph'
            cells.append(f'<td style="background:{band(r["tw"])[2]}" title="{tip}">{r["tw"]:.0f}</td>')
        body.append(f'<tr><th>{d:%a %b %d}</th>{"".join(cells)}<td class="n">{n}</td></tr>')
    legend = " ".join(f'<span style="background:{c}">{lab} (≤{u}°F)</span>' if u < 999
                      else f'<span style="background:{c}">{lab}</span>' for u, lab, c in BANDS)
    now = dt.datetime.now().strftime("%b %d %H:%M")
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{SITE} Snowmaking Outlook</title>
<style>
 body{{font:14px system-ui;margin:16px;color:#111}} table{{border-collapse:collapse}}
 td,th{{padding:4px 6px;text-align:center;border:1px solid #fff}} th{{font-weight:600;background:#f3f4f6}}
 td.n{{font-weight:700;background:#e5e7eb}} .legend span{{padding:2px 8px;margin-right:4px;border-radius:3px}}
 .wrap{{overflow-x:auto}}
</style>
<h1>{SITE} Snowmaking Outlook</h1>
<p>Hourly wet-bulb (°F). Last column = hours ≤ {THRESHOLD_F}°F. HRRR ~48 h, then GFS. Generated {now}.</p>
<p class=legend>{legend}</p>
<div class=wrap><table><tr><th></th>{head}<th>≤{THRESHOLD_F}</th></tr>{"".join(body)}</table></div>
"""

if __name__ == "__main__":
    html = render(blend(fetch()))
    open("index.html", "w").write(html)
    print("wrote index.html")
