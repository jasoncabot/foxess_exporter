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
FOX_HOST=192.168.1.207 python3 foxess_exporter.py
curl localhost:9877/metrics
```

A multi-arch image (amd64 and arm64) is published to GHCR on every push to `main`:

```sh
docker run -d -p 9877:9877 -e FOX_HOST=192.168.1.207 ghcr.io/jasoncabot/foxess_exporter:latest
```

To build locally instead, set `FOX_HOST` in `compose.yaml` and run `docker compose up -d --build`.

On TrueNAS SCALE, `truenas-app.yaml` is a hardened custom-app definition that uses the published image, with a read-only filesystem, all capabilities dropped, and a non-root user.

Add the scrape job to Prometheus (see `prometheus-scrape.yaml`):

```yaml
  - job_name: foxess
    scrape_interval: 15s
    static_configs:
      - targets: ["<exporter-host>:9877"]
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

Plus grid and EPS voltage, current and frequency, inverter and BMS temperatures, and BMS charge/discharge limits.

Example PromQL for daily solar generation:

```promql
increase(foxess_solar_energy_kwh_total[1d])
```

## Notes

- The inverter rejects reads that span unmapped registers, so the exporter reads several small blocks instead of one large one.
- `foxess_bms_energy_remaining_kwh` uses a ×0.001 scale. That's inferred from SoC and not yet confirmed.
