# foxess_exporter

A Prometheus exporter for Fox ESS H1 G2 hybrid inverters that reads the inverter directly over Modbus TCP on your LAN, so it doesn't depend on the Fox cloud.

- One Python file using only the standard library.
- Read-only: it only reads holding registers and never writes to the inverter.
- Tested on an `H1-3.7-E-G2-WL`. The register map comes from [nathanmarlor/foxess_modbus](https://github.com/nathanmarlor/foxess_modbus) (H1 G2 profile).

## Configuration

| Env var       | Default | Description                  |
|---------------|---------|------------------------------|
| `FOX_HOST`    | –       | Inverter IP address (required) |
| `FOX_PORT`    | `502`   | Modbus TCP port              |
| `FOX_UNIT`    | `247`   | Modbus unit ID               |
| `LISTEN_PORT` | `9877`  | Port to serve `/metrics` on  |

## Running

```sh
FOX_HOST=192.168.1.83 python3 foxess_exporter.py
curl localhost:9877/metrics
```

To build and run locally, set `FOX_HOST` in `compose.yaml` and run `docker compose up -d --build`.

`GET /healthz` returns `ok` without contacting the inverter. Use it for liveness checks, and use `foxess_up` for inverter connectivity.

## Deploying to the NAS

`deploy.yaml` is the stack definition. It pulls `ghcr.io/jasoncabot/foxess_exporter:main`, which `.github/workflows/build.yml` builds and pushes on every push to `main`. The container runs with a read-only filesystem as a non-root user, with all capabilities dropped, and keeps no state on disk. It's published on port `30877`.

Add the scrape job to Prometheus (see `prometheus-scrape.yaml`):

```yaml
  - job_name: foxess
    scrape_interval: 15s
    static_configs:
      - targets: ["<nas>:30877"]
```

## Metrics

| Metric | Notes |
|---|---|
| `foxess_up` | `1` if the last read succeeded |
| `foxess_info{model,master_version,…}` | Inverter model and firmware |
| `foxess_pv_{voltage_volts,current_amps,power_watts}{string}` | Per PV string |
| `foxess_grid_ct_power_watts` | Positive when exporting, negative when importing |
| `foxess_load_power_watts` | House load |
| `foxess_battery_power_watts` | Positive when discharging, negative when charging |
| `foxess_battery_{soc,soh}_percent`, `foxess_battery_temperature_celsius` | |
| `foxess_inverter_state{state}` | `standby`, `operation`, `fault`, `off_grid` |
| `foxess_<x>_energy_kwh_total` | Lifetime counters for solar, battery charge/discharge, grid import/export, load, input and output (0.1 kWh resolution) |
| `foxess_<x>_energy_today_kwh` | Today's totals; reset at the inverter's midnight |
| `foxess_work_mode`, `foxess_{min,max}_soc_percent` | Current settings |
| `foxess_alarm{alarm}` | Named alarms from the Fox protocol: grid outage, over-temperature, insulation, meter or BMS lost, and so on |
| `foxess_battery_{capacity,design_capacity,stored}_kwh`, `foxess_battery_modules` | Battery size, energy stored now, and module count |
| `foxess_bms_cell_voltage_volts{bound}` | Highest and lowest cell voltage (0.1 V resolution) |
| `foxess_charge_period_enabled{period,start,end}` | Forced grid-charge time slots |
| `foxess_{export,import}_limit_watts`, `foxess_remote_control_enabled`, `foxess_meter_connected{meter}` | Grid limits, remote control and meter status |
| `foxess_rated_power_watts`, `foxess_bms_info`, `foxess_info{serial,protocol_version,…}` | Ratings and firmware |

Plus grid and EPS voltage, current and frequency, inverter and BMS temperatures, and BMS charge/discharge limits.

Example PromQL for daily solar generation:

```promql
increase(foxess_solar_energy_kwh_total[1d])
```

## Notes

- The inverter rejects reads that span unmapped registers, so the exporter reads several small blocks instead of one large one.
- Settings, identity and battery capacity rarely change, so they're re-read every 5 minutes. Live values are read on every scrape.
- Register 37632 is documented as "remain energy", but it always equals the design capacity, so it's exported as `foxess_battery_capacity_kwh`. Stored energy is capacity × SoC.
- Alarm names come from the FoxESS Modbus protocol V1.05.04.00. Register 37636 (BMS force-charge flag) and the BMS fault registers 37626–37630 don't respond on H1 G2 firmware 3.39, so they aren't read.

## Grafana dashboard

`dashboards/foxess.json` is a ready-made dashboard. To add it, go to **Dashboards → New → Import**, upload the file, and pick your Prometheus data source. It uses only built-in panels. The animated power-flow diagram is a Canvas panel and was tested on Grafana 12.1.

From top to bottom:

1. **Right now:** a live power-flow diagram (solar, grid, battery and home around the inverter), battery level, and system status.
2. **Today:** energy made, used, bought and sold; self-powered % and solar kept %; money saved; power and battery charts.
3. **Last 30 days:** daily solar, and each day's home energy split by where it came from.
4. **This month** and **since installation:** totals, self-powered %, CO₂ avoided, and money saved.
5. **Battery detail:** charge and discharge, temperatures, health, round-trip efficiency, and what the battery has earned.
6. **Solar panels:** power, voltage and current for each string.
7. **Inverter & grid health** (collapsed): grid voltage and frequency, temperatures, firmware, work mode, and faults.

Set your tariff and grid carbon intensity in the variables at the top. `dashboards/build_dashboard.py` generates the JSON, so edit that and re-run it rather than editing the JSON by hand.
