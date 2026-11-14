#!/usr/bin/env python3
"""Generates foxess.json, an importable Grafana dashboard. Run: python3 build_dashboard.py"""

import json
import pathlib

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}

# Entity colours, checked with a colour-blindness/contrast validator on Grafana's dark theme.
SOLAR, HOME, BATTERY, GRID = "#c98500", "#3987e5", "#199e70", "#8f8e86"
SERIES = ["#3987e5", "#d95926", "#199e70"]  # generic categorical order for non-entity series
GOOD, WARN, BAD = "#3d9a50", "#e0a100", "#d64545"

_id = 0
panels = []


def nid():
    global _id
    _id += 1
    return _id


def target(expr, legend="", instant=False, interval=None, ref="A"):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant}
    if interval:
        t["interval"] = interval
    return t


def targets(*specs, **kw):
    return [target(e, l, ref=chr(65 + i), **kw) for i, (e, l) in enumerate(specs)]


def fixed(color):
    return {"mode": "fixed", "fixedColor": color}


def override(name, color, **props):
    p = [{"id": "color", "value": fixed(color)}]
    p += [{"id": k, "value": v} for k, v in props.items()]
    return {"matcher": {"id": "byName", "options": name}, "properties": p}


def thresholds(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def row(title, y, collapsed=False, children=None):
    panels.append({"type": "row", "id": nid(), "title": title, "collapsed": collapsed,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": children or []})


def place(p, x, y, w, h, into=None):
    p["id"] = nid()
    p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
    p.setdefault("datasource", DS)
    (into if into is not None else panels).append(p)
    return p


def stat(title, tgts, unit, color=None, desc="", decimals=None, spark=False, th=None, mappings=None,
         overrides=None, text_mode="value", min_=None, max_=None, size=None, orientation="auto"):
    defaults = {"unit": unit, "color": fixed(color) if color else {"mode": "thresholds"},
                "thresholds": th or thresholds((None, color or "text")), "mappings": mappings or [],
                "noValue": "—"}
    if decimals is not None:
        defaults["decimals"] = decimals
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value", "graphMode": "area" if spark else "none", "justifyMode": "center",
            "textMode": text_mode, "orientation": orientation, "showPercentChange": False,
            "wideLayout": True}
    if size:
        opts["text"] = {"valueSize": size}
    return {"type": "stat", "title": title, "description": desc, "targets": tgts,
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []}, "options": opts}


def gauge(title, tgts, unit, th, desc="", min_=0, max_=100, decimals=0):
    return {"type": "gauge", "title": title, "description": desc, "targets": tgts,
            "fieldConfig": {"defaults": {"unit": unit, "min": min_, "max": max_, "decimals": decimals,
                                         "color": {"mode": "thresholds"}, "thresholds": th,
                                         "noValue": "—"},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "showThresholdMarkers": False, "showThresholdLabels": False, "minVizHeight": 75,
                        "minVizWidth": 75, "sizing": "auto"}}


def timeseries(title, tgts, unit, desc="", overrides=None, draw="line", fill=0, stack=False, decimals=None,
               min_=None, max_=None, time_from=None, legend_calcs=None, soft_min=None, th_style=None,
               th=None, interval=None, bar_width=0.8):
    custom = {"drawStyle": draw, "lineWidth": 2, "fillOpacity": fill, "gradientMode": "opacity" if fill else "none",
              "lineInterpolation": "smooth", "showPoints": "never", "spanNulls": 300000,
              "axisBorderShow": False, "axisCenteredZero": False, "axisPlacement": "auto",
              "barAlignment": -1, "barWidthFactor": bar_width,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "thresholdsStyle": {"mode": th_style or "off"}, "pointSize": 8}
    if soft_min is not None:
        custom["axisSoftMin"] = soft_min
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"},
                "thresholds": th or thresholds((None, "transparent"))}
    for k, v in (("decimals", decimals), ("min", min_), ("max", max_)):
        if v is not None:
            defaults[k] = v
    p = {"type": "timeseries", "title": title, "description": desc, "targets": tgts,
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": {"legend": {"displayMode": "list" if not legend_calcs else "table", "placement": "bottom",
                                "showLegend": True, "calcs": legend_calcs or []},
                     "tooltip": {"mode": "multi", "sort": "none"}}}
    if time_from:
        p["timeFrom"] = time_from
    if interval:
        p["interval"] = interval
    return p


def text(title, content):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


ON_OFF = lambda on, off, on_col, off_col: [  # noqa: E731
    {"type": "value", "options": {"1": {"text": on, "color": on_col, "index": 0},
                                  "0": {"text": off, "color": off_col, "index": 1}}}]

# Shared expressions
SOLAR_W = "sum(foxess_pv_power_watts)"
# Battery power with readings at or under $battery_noise_w zeroed (a dashboard variable, so it can be tuned without a
# rebuild): the inverter-side figure idles at ±0.1 A (~40-85 W) of noise when the battery is resting, which would
# otherwise show as phantom charging.
BATT_W = "(foxess_battery_power_watts * (abs(foxess_battery_power_watts) > bool $battery_noise_w))"
# Grid power with readings under 30 W zeroed: the inverter hunts around zero when the battery covers the house.
GRID_W = "(foxess_grid_ct_power_watts * (abs(foxess_grid_ct_power_watts) > bool 30))"
# Ratios only once the denominator is at least 0.5 kWh; energy counters step in 0.1 kWh, so smaller is noise.
MIN_KWH = 0.5
SELF_SUFF_TODAY = ("clamp_min(clamp_max((foxess_load_energy_today_kwh - foxess_grid_import_energy_today_kwh)"
                   f" / foxess_load_energy_today_kwh, 1), 0) * 100 and foxess_load_energy_today_kwh >= {MIN_KWH}")
SAVED_TODAY = ("clamp_min(foxess_load_energy_today_kwh - foxess_grid_import_energy_today_kwh, 0) * $import_price"
               " + foxess_grid_export_energy_today_kwh * $export_price")
SELF_USE_TODAY = ("clamp_min(clamp_max((foxess_solar_energy_today_kwh - foxess_grid_export_energy_today_kwh)"
                  f" / foxess_solar_energy_today_kwh, 1), 0) * 100 and foxess_solar_energy_today_kwh >= {MIN_KWH}")


def daily(m):
    """A completed day's total, taken from the inverter's own today counter (which resets at its local midnight).
    The 22h window skips the first two hours after UTC midnight, so clock drift and BST can't pick up the
    previous day's total before the inverter resets."""
    return f"max_over_time({m}[22h])"


def month_total(m):
    """This month's total: each completed day's final value since the 1st, plus today so far."""
    return f"((sum(sum_over_time({daily(m)}[$__range:1d])) or vector(0)) + sum({m}))"


def power_flow():
    """Canvas diagram: Solar, Grid, Battery and Home around the inverter, with animated arrows whose
    thickness follows the power. Battery and grid get one arrow per direction; the idle one has zero width."""
    def pos(left, top, w, h):
        return {"constraint": {"horizontal": "scale", "vertical": "scale"},
                "placement": {"left": left, "top": top, "right": 100 - left - w, "bottom": 100 - top - h}}

    def box(name, color, left, top, w, h, connections=()):
        return {"type": "rectangle", "name": name, **pos(left, top, w, h),
                "config": {"text": {"mode": "fixed", "fixed": ""}, "align": "center", "valign": "middle",
                           "size": 14, "color": {"fixed": "text"}},
                "background": {"color": {"fixed": color + "26"}},
                "border": {"color": {"fixed": color}, "width": 2, "radius": 18},
                "connections": list(connections)}

    def label(name, txt, color, left, top, w, h, size):
        return {"type": "text", "name": name, **pos(left, top, w, h),
                "config": {"text": {"mode": "fixed", "fixed": txt}, "align": "center", "valign": "middle",
                           "size": size, "color": {"fixed": color}},
                "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "transparent"}}}

    def value(name, field, color, left, top, w, h, size):
        return {"type": "metric-value", "name": name, **pos(left, top, w, h),
                "config": {"text": {"mode": "field", "field": field, "fixed": ""}, "align": "center",
                           "valign": "middle", "size": size, "color": {"fixed": color}},
                "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "transparent"}}}

    def arrow(target, field, color, src, dst):
        return {"targetName": target, "path": "straight", "source": {"x": src[0], "y": src[1]},
                "target": {"x": dst[0], "y": dst[1]}, "color": {"fixed": color},
                "size": {"fixed": 2, "min": 0, "max": 12, "field": field},
                "lineStyle": {"style": "dashed", "animate": True}, "direction": "forward", "vertices": []}

    # Node boxes (percent of panel). Hub = inverter in the middle.
    W, H = 26, 26
    sx, sy = 37, 2      # solar, top
    gx, gy = 2, 37      # grid, left
    bx, by = 72, 37     # battery, right
    hx, hy = 37, 72     # home, bottom
    elements = [
        box("Hub", GRID, 44, 44, 12, 12, connections=[
            arrow("Home node", "Home flow", HOME, (0, -1), (0, 1)),
            arrow("Grid node", "Selling", GRID, (-1, -0.4), (1, -0.25)),
            arrow("Battery node", "Charging", BATTERY, (1, -0.4), (-1, -0.25))]),
        label("Hub label", "⚡ Inverter", "text", 44, 44, 12, 12, 13),
        box("Solar node", SOLAR, sx, sy, W, H, connections=[arrow("Hub", "Solar flow", SOLAR, (0, -1), (0, 1))]),
        label("Solar label", "☀️ Solar", SOLAR, sx, sy + 2, W, 8, 16),
        value("Solar value", "Solar", "text", sx, sy + 10, W, 14, 30),
        box("Grid node", GRID, gx, gy, W, H, connections=[arrow("Hub", "Buying", GRID, (1, 0.25), (-1, 0.4))]),
        label("Grid label", "🔌 Grid", GRID, gx, gy + 2, W, 8, 16),
        value("Grid value", "Grid", "text", gx, gy + 10, W, 14, 30),
        box("Battery node", BATTERY, bx, by, W, H,
            connections=[arrow("Hub", "Discharging", BATTERY, (-1, 0.25), (1, 0.4))]),
        label("Battery label", "🔋 Battery", BATTERY, bx, by + 2, W, 8, 16),
        value("Battery value", "Battery", "text", bx, by + 9, W, 10, 26),
        value("Battery soc", "Charge", BATTERY, bx, by + 18, W, 7, 16),
        box("Home node", HOME, hx, hy, W, H),
        label("Home label", "🏠 Home", HOME, hx, hy + 2, W, 8, 16),
        value("Home value", "Home", "text", hx, hy + 10, W, 14, 30),
    ]
    # Arrow widths: sqrt so small flows stay visible, plus a floor once anything over 20 W is moving.
    flow = "(sqrt(clamp_min({0}, 0)) + 15 * (clamp_min({0}, 0) > bool 20))".format
    tg = targets((SOLAR_W, "Solar"), ("foxess_load_power_watts", "Home"),
                 (f"abs({GRID_W})", "Grid"), (f"abs({BATT_W})", "Battery"),
                 ("foxess_battery_soc_percent", "Charge"),
                 (flow(SOLAR_W), "Solar flow"), (flow("foxess_load_power_watts"), "Home flow"),
                 (flow(f"-{GRID_W}"), "Buying"), (flow(GRID_W), "Selling"),
                 (flow(f"-{BATT_W}"), "Charging"),
                 (flow(BATT_W), "Discharging"), instant=True)
    flow_fields = ["Solar flow", "Home flow", "Buying", "Selling", "Charging", "Discharging"]
    # When the inverter is offline, show "—" in the boxes (NaN, mapped below) and hide the arrows (0 width)
    # rather than canvas's "Field not found" and default-width arrows.
    for t in tg:
        t["expr"] = f'({t["expr"]}) or on() vector({0 if t["legendFormat"] in flow_fields else "NaN"})'
    return {"type": "canvas", "title": "Where the power is going right now",
            "description": "Moving arrows show which way power is flowing; thicker arrows mean more power. Battery flows of $battery_noise_w W or less and grid flows "
                           "under 30 W are hidden as measurement noise. "
                           "Arrows into the inverter from the grid mean buying, out to the grid mean selling.",
            "targets": tg,
            "fieldConfig": {"defaults": {"unit": "watt", "decimals": 1,
                                         "mappings": [{"type": "special", "options": {
                                             "match": "nan", "result": {"text": "—", "index": 0}}}],
                                         "color": {"mode": "fixed", "fixedColor": "text"}},
                            "overrides": [{"matcher": {"id": "byName", "options": "Charge"},
                                           "properties": [{"id": "unit", "value": "percent"},
                                                          {"id": "decimals", "value": 0},
                                                          {"id": "max", "value": 100}]},
                                          {"matcher": {"id": "byNames", "options": {"mode": "include", "names": flow_fields}},
                                           "properties": [{"id": "min", "value": 0}, {"id": "max", "value": 85}]}]},
            "options": {"inlineEditing": False, "showAdvancedTypes": True, "panZoom": False,
                        "infinitePan": False,
                        "root": {"type": "frame", "name": "Element root", "elements": elements,
                                 "background": {"color": {"fixed": "transparent"}},
                                 "border": {"color": {"fixed": "transparent"}},
                                 "constraint": {"horizontal": "left", "vertical": "top"},
                                 "placement": {"left": 0, "top": 0, "width": 100, "height": 100, "rotation": 0}}}}

# ── Right now ────────────────────────────────────────────────────────────────
y = 0
row("☀️  Right now", y)
y += 1
place(power_flow(), 0, y, 14, 12)
place(gauge("🔋 Battery level", targets(("foxess_battery_soc_percent", "Battery"), instant=True), "percent",
            thresholds((None, WARN), (20, BATTERY)),
            desc="How full the battery is. It stops discharging at the minimum level set on the inverter."),
      14, y, 5, 6)
place(stat("🔋 Battery is…",
           targets((f"clamp_min(-{BATT_W}, 0)", "Charging"),
                   (f"clamp_min({BATT_W}, 0)", "Powering home"), instant=True),
           "watt", BATTERY, text_mode="value_and_name", decimals=1, size=22,
           desc="Charging = storing spare energy. Powering home = the battery is supplying the house."),
      14, y + 6, 5, 6)
place(stat("⚡ Grid",
           targets((f"clamp_min(-{GRID_W}, 0)", "Buying"),
                   (f"clamp_min({GRID_W}, 0)", "Selling"), instant=True),
           "watt", GRID, text_mode="value_and_name", decimals=1, size=22,
           desc="Buying = importing from the grid (costs money). Selling = exporting spare solar."),
      19, y + 6, 5, 6)
place(stat("✅ System",
           [target("foxess_up", "Inverter link", ref="A", instant=True),
            target("1 - foxess_inverter_state{state=\"off_grid\"}", "Mains power", ref="B", instant=True),
            target("1 - clamp_max(sum(foxess_inverter_state{state=\"fault\"}) + sum(foxess_alarm), 1)", "Faults",
                   ref="C", instant=True)],
           "none", text_mode="value_and_name", size=18,
           overrides=[
               {"matcher": {"id": "byName", "options": "Inverter link"},
                "properties": [{"id": "mappings", "value": ON_OFF("Connected", "Offline", GOOD, BAD)}]},
               {"matcher": {"id": "byName", "options": "Mains power"},
                "properties": [{"id": "mappings", "value": ON_OFF("On", "Power cut – on battery", GOOD, WARN)}]},
               {"matcher": {"id": "byName", "options": "Faults"},
                "properties": [{"id": "mappings", "value": ON_OFF("None", "Fault!", GOOD, BAD)}]}],
           desc="Whether the exporter can reach the inverter, whether mains power is on, and any inverter faults."),
      19, y, 5, 6)
y += 12

# ── Today ────────────────────────────────────────────────────────────────────
row("📅  Today", y)
y += 1
today = [
    ("☀️ Solar made", "foxess_solar_energy_today_kwh", SOLAR, "Energy the panels generated today."),
    ("🏠 Home used", "foxess_load_energy_today_kwh", HOME, "Everything the house used today."),
    ("⚡ Bought", "foxess_grid_import_energy_today_kwh", GRID, "Energy imported (paid for) today."),
    ("💷 Sold", "foxess_grid_export_energy_today_kwh", GRID, "Spare solar exported today."),
]
for i, (t, e, c, d) in enumerate(today):
    place(stat(t, targets((e, t), instant=True), "kwatth", c, decimals=1, desc=d), i * 3, y, 3, 5)
place(gauge("🌱 Self-powered", targets((SELF_SUFF_TODAY, "Self-powered"), instant=True), "percent",
            thresholds((None, WARN), (60, GOOD)),
            desc="Self-sufficiency: share of today's home energy that came from your own solar and battery "
                 "instead of the grid. Shows — until the house has used 0.5 kWh."),
      12, y, 4, 5)
place(gauge("♻️ Solar kept", targets((SELF_USE_TODAY, "Solar kept"), instant=True), "percent",
            thresholds((None, WARN), (80, GOOD)),
            desc="Self-consumption: share of today's solar used in the house or stored in the battery, "
                 "rather than sold to the grid. Shows — until 0.5 kWh of solar."),
      16, y, 4, 5)
place(stat("💰 Saved today", targets((SAVED_TODAY, "Saved"), instant=True), "currencyGBP", GOOD, decimals=2,
           desc="Energy you didn't have to buy (at the import price) plus export earnings (at the export "
                "price). Set your tariff in the dashboard variables at the top."),
      20, y, 4, 5)
y += 5
place(timeseries("Where the power is flowing",
                 targets((SOLAR_W, "Solar"), ("foxess_load_power_watts", "Home"),
                         ("foxess_battery_power_watts", "Battery"), ("-foxess_grid_ct_power_watts", "Grid")),
                 "watt", fill=0, decimals=2,
                 desc="Above the zero line, Battery and Grid are supplying the house. Below it, the battery "
                      "is charging and spare solar is being sold to the grid.",
                 overrides=[override("Solar", SOLAR, **{"custom.fillOpacity": 25}),
                            override("Home", HOME),
                            override("Battery", BATTERY),
                            override("Grid", GRID, **{"custom.lineStyle": {"fill": "dash", "dash": [10, 6]}})]),
      0, y, 16, 10)
place(timeseries("Battery level",
                 targets(("foxess_battery_soc_percent", "Battery"), ("foxess_min_soc_percent", "Reserve")),
                 "percent", fill=30, min_=0, max_=100, decimals=0,
                 desc="How full the battery has been. The dashed line is the reserve it won't go below.",
                 overrides=[override("Battery", BATTERY),
                            override("Reserve", GRID, **{"custom.fillOpacity": 0, "custom.lineWidth": 1,
                                                         "custom.lineStyle": {"fill": "dash", "dash": [4, 4]}})]),
      16, y, 8, 10)
y += 10

# ── Last 30 days ─────────────────────────────────────────────────────────────
row("📈  Last 30 days", y)
y += 1
place(timeseries("Solar made each day", targets((daily("foxess_solar_energy_today_kwh"), "Solar")),
                 "kwatth", draw="bars", fill=80, decimals=1, time_from="30d", interval="1d", min_=0,
                 desc="Daily solar generation for each completed day.", overrides=[override("Solar", SOLAR)]),
      0, y, 12, 9)
place(timeseries("Where the home's energy came from each day",
                 targets((f"clamp_min({daily('foxess_load_energy_today_kwh')}"
                          f" - {daily('foxess_grid_import_energy_today_kwh')}, 0)", "Own solar & battery"),
                         (daily("foxess_grid_import_energy_today_kwh"), "Bought from grid")),
                 "kwatth", draw="bars", fill=80, stack=True, decimals=1, time_from="30d", interval="1d", min_=0,
                 desc="Each bar is a completed day's total home use, split by where it came from.",
                 overrides=[override("Own solar & battery", BATTERY), override("Bought from grid", GRID)]),
      12, y, 12, 9)
y += 9

# ── This month ───────────────────────────────────────────────────────────────
row("🗓️  This month", y)
y += 1
solar_m, load_m, import_m, export_m = (month_total(f"foxess_{n}_energy_today_kwh")
                                       for n in ("solar", "load", "grid_import", "grid_export"))
month = [
    ("☀️ Solar made", solar_m, "kwatth", SOLAR, 1, "Generated since the 1st."),
    ("🏠 Home used", load_m, "kwatth", HOME, 1, "House use since the 1st."),
    ("⚡ Bought from grid", import_m, "kwatth", GRID, 1, "Imported since the 1st."),
    ("💷 Sold to grid", export_m, "kwatth", GRID, 1, "Exported since the 1st."),
    ("🌱 Self-powered", f"clamp_min((1 - {import_m} / {load_m}) * 100, 0) and {load_m} >= {MIN_KWH}",
     "percent", GOOD, 0, "Share of this month's home energy not bought from the grid."),
    ("💰 Saved", f"clamp_min({load_m} - {import_m}, 0) * $import_price + {export_m} * $export_price",
     "currencyGBP", GOOD, 2, "This month's saving at your tariff."),
]
for i, (t, e, u, c, dec, d) in enumerate(month):
    p = stat(t, targets((e, t), instant=True), u, c, decimals=dec, desc=d)
    p["timeFrom"] = "now/M"
    p["hideTimeOverride"] = True
    place(p, i * 4, y, 4, 4)
y += 4

# ── Since installation ───────────────────────────────────────────────────────
row("🏆  Since installation", y)
y += 1
lifetime = [
    ("☀️ Solar made", "foxess_solar_energy_kwh_total", "kwatth", SOLAR, 1, "Total generated since install."),
    ("🏠 Home used", "foxess_load_energy_kwh_total", "kwatth", HOME, 1, "Total house use since install."),
    ("💷 Sold to grid", "foxess_grid_export_energy_kwh_total", "kwatth", GRID, 1, "Total exported since install."),
    ("🌱 Self-powered", "(1 - foxess_grid_import_energy_kwh_total / foxess_load_energy_kwh_total) * 100",
     "percent", GOOD, 0, "Share of all home energy since install that didn't come from the grid."),
    ("🌍 CO₂ avoided", "foxess_solar_energy_kwh_total * $carbon_intensity", "masskg", GOOD, 1,
     "Solar generated × grid carbon intensity (set in the dashboard variables)."),
    ("💰 Saved", "clamp_min(foxess_load_energy_kwh_total - foxess_grid_import_energy_kwh_total, 0) * $import_price"
                " + foxess_grid_export_energy_kwh_total * $export_price", "currencyGBP", GOOD, 2,
     "Rough lifetime saving at today's tariff (set in the dashboard variables)."),
]
for i, (t, e, u, c, dec, d) in enumerate(lifetime):
    place(stat(t, targets((e, t), instant=True), u, c, decimals=dec, desc=d), i * 4, y, 4, 4)
y += 4

# ── Battery detail ───────────────────────────────────────────────────────────
row("🔋  Battery detail", y)
y += 1
place(timeseries("Battery charging and discharging",
                 targets(("clamp_min(foxess_battery_power_watts, 0)", "Powering home"),
                         ("clamp_max(foxess_battery_power_watts, 0)", "Charging")),
                 "watt", fill=35, decimals=2, legend_calcs=["max", "mean"],
                 desc="Above zero: the battery is supplying the house. Below zero: it's charging.",
                 overrides=[override("Powering home", BATTERY), override("Charging", HOME)]),
      0, y, 12, 8)
place(timeseries("Battery temperature",
                 targets(("foxess_battery_temperature_celsius", "Pack sensor"),
                         ("foxess_bms_cell_temperature_celsius{bound=\"high\"}", "Warmest cell"),
                         ("foxess_bms_cell_temperature_celsius{bound=\"low\"}", "Coolest cell")),
                 "celsius", decimals=1, legend_calcs=["min", "max"],
                 desc="Warmest/coolest cell are the actual cell temperatures. The pack sensor sits elsewhere in the "
                      "battery (likely the control board) and usually reads warmer. Lithium cells are happiest "
                      "between about 15 and 30 °C.",
                 overrides=[override(n, SERIES[i]) for i, n in enumerate(("Pack sensor", "Warmest cell", "Coolest cell"))]),
      12, y, 12, 8)
y += 8
batt = [
    ("Health", "foxess_battery_soh_percent", "percent", 0, "Capacity compared with new (100% = as new)."),
    ("Stored now", "foxess_battery_stored_kwh", "kwatth", 2, "Energy in the battery right now (capacity × charge level)."),
    ("Capacity", "foxess_battery_capacity_kwh", "kwatth", 2, "How much the battery holds when full. Falls slowly as it ages."),
    ("Round-trip efficiency", "foxess_battery_discharge_energy_kwh_total / foxess_battery_charge_energy_kwh_total"
     " * 100 and foxess_battery_charge_energy_kwh_total >= 5", "percent", 0, "Energy got back out of the battery for every unit put in, since install. "
                             "85–95% is typical; it reads low until the battery has done a few full cycles."),
    ("Battery has earned", "foxess_battery_discharge_energy_kwh_total * $import_price"
     " - foxess_battery_charge_energy_kwh_total * $export_price", "currencyGBP", 2,
     "Since install: energy the battery supplied (valued at the import price) minus what it stored "
     "(valued at the export price you'd otherwise have earned)."),
    ("Cell voltage", None, "volt", 1, "Highest and lowest cell voltage (0.1 V resolution). A widening gap "
                                     "between them means the cells are drifting out of balance."),
]
for i, (t, e, u, dec, d) in enumerate(batt):
    if e is None:
        p = stat(t, targets(('foxess_bms_cell_voltage_volts{bound="high"}', "Highest"),
                            ('foxess_bms_cell_voltage_volts{bound="low"}', "Lowest")),
                 u, BATTERY, decimals=dec, desc=d, text_mode="value_and_name", size=20)
    else:
        p = stat(t, targets((e, t)), u, BATTERY, decimals=dec, desc=d, spark=u in ("percent", "kwatth"))
    place(p, i * 4, y, 4, 4)
y += 4

# ── Solar panels ─────────────────────────────────────────────────────────────
row("🌞  Solar panels", y)
y += 1
strings = [override("String 1", SERIES[0]), override("String 2", SERIES[1])]
place(timeseries("Power per panel string", targets(("foxess_pv_power_watts", "String {{string}}")), "watt",
                 fill=20, decimals=2, legend_calcs=["max", "mean"], overrides=strings,
                 desc="Each string is a separate run of panels, e.g. different roof faces."),
      0, y, 12, 8)
place(timeseries("Voltage per string", targets(("foxess_pv_voltage_volts", "String {{string}}")), "volt",
                 decimals=0, overrides=strings, desc="Panel string voltage. Near zero at night."),
      12, y, 6, 8)
place(timeseries("Current per string", targets(("foxess_pv_current_amps", "String {{string}}")), "amp",
                 decimals=1, overrides=strings, desc="Panel string current."),
      18, y, 6, 8)
y += 8

# ── Inverter & grid health (collapsed) ───────────────────────────────────────
health = []
hy = y + 1
place(stat("Inverter", [target("foxess_info", "{{model}}", instant=True)], "none", GOOD, text_mode="name",
           desc="Inverter model.", size=20), 0, hy, 4, 4, into=health)
place(stat("Firmware", [target("foxess_info", "Master {{master_version}} · Slave {{slave_version}} · "
                                              "Manager {{manager_version}}", instant=True)],
           "none", "text", text_mode="name", size=14, desc="Inverter firmware versions."), 4, hy, 6, 4, into=health)
place(stat("Work mode", targets(("foxess_work_mode", "Mode")), "none", text_mode="value", size=20,
           mappings=[{"type": "value", "options": {"0": {"text": "Self use", "color": GOOD},
                                                   "1": {"text": "Feed-in first", "color": SOLAR},
                                                   "2": {"text": "Back-up", "color": WARN}}}],
           desc="Self use: power the house first, then charge the battery, then export."), 10, hy, 4, 4, into=health)
place(stat("Fault registers", targets(("foxess_fault_code", "{{register}}")), "none", GOOD,
           th=thresholds((None, GOOD), (1, BAD)), text_mode="value_and_name", size=16,
           desc="Raw inverter fault bitmasks. Anything other than 0 means the inverter is reporting a fault."),
      14, hy, 6, 4, into=health)
place(stat("Exporter read time", targets(("foxess_scrape_duration_seconds", "Read time")), "s", "text",
           decimals=2, spark=True, desc="How long each Modbus read of the inverter takes."), 20, hy, 4, 4, into=health)
hy += 4
place(timeseries("Grid voltage", targets(("foxess_grid_voltage_volts", "Grid"), ("foxess_eps_voltage_volts", "Backup")),
                 "volt", decimals=1, legend_calcs=["min", "max"],
                 desc="UK mains is 230 V, allowed range 216–253 V.",
                 overrides=[override("Grid", SERIES[0]), override("Backup", SERIES[1])]), 0, hy, 8, 8, into=health)
place(timeseries("Grid frequency", targets(("foxess_grid_frequency_hertz", "Grid"),
                                           ("foxess_eps_frequency_hertz", "Backup")),
                 "hertz", decimals=2, legend_calcs=["min", "max"], desc="Nominal 50 Hz.",
                 overrides=[override("Grid", SERIES[0]), override("Backup", SERIES[1])]), 8, hy, 8, 8, into=health)
place(timeseries("Inverter temperature", targets(("foxess_inverter_temperature_celsius", "Inverter"),
                                                 ("foxess_ambient_temperature_celsius", "Inside casing")),
                 "celsius", decimals=1, legend_calcs=["max"],
                 overrides=[override("Inverter", SERIES[0]), override("Inside casing", SERIES[1])]),
      16, hy, 8, 8, into=health)
hy += 8
place(timeseries("Inverter AC side", targets(("foxess_inverter_power_watts", "Inverter"),
                                             ("foxess_eps_power_watts", "Backup output"),
                                             ("foxess_ct2_power_watts", "CT2")),
                 "watt", decimals=2, desc="Inverter AC power, backup (EPS) output and the second CT clamp.",
                 overrides=[override(n, SERIES[i]) for i, n in enumerate(("Inverter", "Backup output", "CT2"))]),
      0, hy, 12, 8, into=health)
place(timeseries("Battery current", targets(("foxess_battery_current_amps", "Inverter side"),
                                            ("foxess_bms_current_amps", "BMS")),
                 "amp", decimals=1, desc="Battery current as seen by the inverter and by the battery itself.",
                 overrides=[override("Inverter side", SERIES[0]), override("BMS", SERIES[1])]),
      12, hy, 12, 8, into=health)
hy += 8
alarms = stat("Active alarms", [target("foxess_alarm == 1", "{{alarm}}", instant=True)], "none", BAD,
              text_mode="value_and_name", size=16, desc="Named inverter alarms from the Fox protocol.",
              mappings=[{"type": "value", "options": {"1": {"text": "Active", "color": BAD, "index": 0}}}])
alarms["fieldConfig"]["defaults"]["noValue"] = "All clear"
alarms["fieldConfig"]["defaults"]["color"] = fixed(GOOD)
place(alarms, 0, hy, 6, 5, into=health)
place(stat("Battery", [target("foxess_battery_modules", "Modules", ref="A", instant=True),
                       target("foxess_battery_design_capacity_kwh", "Design capacity (kWh)", ref="B", instant=True),
                       target("foxess_battery_capacity_amp_hours", "Capacity (Ah)", ref="C", instant=True)],
           "none", BATTERY, text_mode="value_and_name", size=18, desc="Battery modules and rated capacity."),
      6, hy, 6, 5, into=health)
place(stat("Grid limits", [target("foxess_export_limit_watts", "Export", ref="A", instant=True),
                           target("foxess_import_limit_watts", "Import", ref="B", instant=True)],
           "watt", GRID, text_mode="value_and_name", size=18, decimals=1,
           desc="Maximum power the inverter will export to, or import from, the grid."), 12, hy, 4, 5, into=health)
place(stat("Forced grid charging", [target("foxess_charge_period_enabled", "Slot {{period}}: {{start}}–{{end}}",
                                           instant=True)],
           "none", text_mode="value_and_name", size=16,
           mappings=[{"type": "value", "options": {"0": {"text": "Off", "color": GOOD, "index": 0},
                                                   "1": {"text": "On", "color": WARN, "index": 1}}}],
           desc="Time slots when the inverter charges the battery from the grid (e.g. on a cheap overnight tariff)."),
      16, hy, 4, 5, into=health)
place(stat("Control", [target("foxess_remote_control_enabled", "Remote control", ref="A", instant=True),
                       target('foxess_meter_connected{meter="1"}', "Grid meter", ref="B", instant=True)],
           "none", text_mode="value_and_name", size=16,
           overrides=[{"matcher": {"id": "byName", "options": "Remote control"},
                       "properties": [{"id": "mappings", "value": ON_OFF("On", "Off", WARN, GOOD)}]},
                      {"matcher": {"id": "byName", "options": "Grid meter"},
                       "properties": [{"id": "mappings", "value": ON_OFF("Connected", "Missing", GOOD, BAD)}]}],
           desc="Whether something external is controlling the inverter, and whether the grid meter is connected."),
      20, hy, 4, 5, into=health)
row("⚙️  Inverter & grid health", y, collapsed=True, children=health)

dashboard = {
    "__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "description": "", "type": "datasource",
                  "pluginId": "prometheus", "pluginName": "Prometheus"}],
    "__requires": [{"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.4.0"},
                   {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"}],
    "title": "Home Solar",
    "uid": "foxess-home-solar",
    "description": "Fox ESS solar, battery and grid, from foxess_exporter",
    "tags": ["solar", "foxess", "energy"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "30s",
    "time": {"from": "now/d", "to": "now"},
    "timepicker": {},
    "schemaVersion": 39,
    "version": 1,
    "links": [{"title": "foxess_exporter", "type": "link", "icon": "external link", "targetBlank": True,
               "url": "https://github.com/jasoncabot/foxess_exporter"}],
    "templating": {"list": [
        {"type": "textbox", "name": "battery_noise_w", "label": "Ignore battery flow ≤ W", "query": "85",
         "current": {"text": "85", "value": "85"}, "hide": 0},
        {"type": "textbox", "name": "import_price", "label": "Import price £/kWh", "query": "0.2875",
         "current": {"text": "0.2875", "value": "0.2875"}, "hide": 0},
        {"type": "textbox", "name": "export_price", "label": "Export price £/kWh", "query": "0.15",
         "current": {"text": "0.15", "value": "0.15"}, "hide": 0},
        {"type": "textbox", "name": "carbon_intensity", "label": "Grid CO₂ kg/kWh", "query": "0.207",
         "current": {"text": "0.207", "value": "0.207"}, "hide": 0},
    ]},
    "annotations": {"list": []},
    "panels": panels,
}

out = pathlib.Path(__file__).with_name("foxess.json")
out.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {out} ({len(panels)} top-level panels)")
