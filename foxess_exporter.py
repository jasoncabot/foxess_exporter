#!/usr/bin/env python3
"""Prometheus exporter for Fox ESS H1 G2 inverters over Modbus TCP (LAN).

Read-only: only ever issues function code 3 (read holding registers).
Register map from https://github.com/nathanmarlor/foxess_modbus (H1_G2 profile),
verified against an H1-3.7-E-G2-WL.

Config (env vars):
  FOX_HOST      inverter IP (required)
  FOX_PORT      Modbus TCP port (default 502)
  FOX_UNIT      Modbus unit id (default 247)
  LISTEN_PORT   HTTP port for /metrics (default 9877)
"""

import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FOX_HOST = os.environ["FOX_HOST"]
FOX_PORT = int(os.environ.get("FOX_PORT", "502"))
FOX_UNIT = int(os.environ.get("FOX_UNIT", "247"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9877"))
TIMEOUT = 5

# Contiguous holding-register blocks to read each scrape: (start, count).
# Kept small because the inverter silently ignores (and so times out on) reads that touch any unsupported
# register. The 41xxx settings are read one at a time: block reads there are unreliable on the H1 G2.
#
# The logger answers most reads in ~20 ms but sometimes takes a second or more, so registers that rarely change
# (identity, ratings, settings, battery capacity) are only re-read every SLOW_INTERVAL seconds.
FAST_BLOCKS = [(31000, 30), (32000, 24), (37609, 12), (39063, 3), (39067, 3), (39070, 4), (39279, 4)]
SLOW_BLOCKS = [(30000, 24), (36001, 3), (37003, 2), (37032, 10), (37624, 1), (37632, 4), (38801, 1),
               (38901, 1), (39000, 1), (39053, 6), (44000, 1), (46501, 2)]
SLOW_BLOCKS += [(a, 1) for a in (41000, 41001, 41002, 41003, 41004, 41005, 41006, 41009, 41010, 41011, 41012)]
SLOW_INTERVAL = 300

# Named bits of the alarm registers, from the FoxESS Modbus protocol V1.05.04.00, table 4-1.
ALARMS = {
    39067: {0: "Input string voltage high", 1: "DC arc fault", 2: "String reverse connection",
            7: "Grid power outage", 8: "Abnormal grid voltage", 11: "Abnormal grid frequency",
            14: "Output overcurrent", 15: "Output DC component too high"},
    39068: {0: "Abnormal residual current", 1: "System grounding abnormal", 2: "Low insulation resistance",
            3: "Temperature too high", 9: "Energy storage abnormal", 10: "Islanding",
            14: "Off-grid output overload"},
    39069: {3: "External fan abnormal", 9: "Meter lost", 10: "BMS lost"},
}


class ModbusClient:
    """Minimal Modbus TCP client with a persistent, auto-reconnecting socket."""

    def __init__(self, host, port, unit):
        self.host, self.port, self.unit = host, port, unit
        self.sock = None
        self.tid = 0
        self.lock = threading.Lock()

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by inverter")
            buf += chunk
        return buf

    def _read_once(self, addr, count):
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=TIMEOUT)
        self.tid = (self.tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", 3, addr, count)
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
        tid, _, length, _ = struct.unpack(">HHHB", self._recv(7))
        body = self._recv(length - 1)
        if tid != self.tid:
            raise ConnectionError(f"transaction id mismatch ({tid} != {self.tid})")
        if body[0] & 0x80:
            raise ValueError(f"modbus exception {body[1]} reading {addr}+{count}")
        return list(struct.unpack(f">{body[1] // 2}H", body[2:2 + body[1]]))

    def read(self, addr, count):
        with self.lock:
            for attempt in (1, 2):
                try:
                    return self._read_once(addr, count)
                except (OSError, ConnectionError):
                    self.close()
                    if attempt == 2:
                        raise

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def u32(hi, lo):
    return (hi << 16) | lo


def i32(hi, lo):
    v = u32(hi, lo)
    return v - 0x100000000 if v & 0x80000000 else v


def string(r, start, count):
    raw = bytes(b for w in (r(start + i) for i in range(count)) for b in (w >> 8, w & 0xFF))
    return raw.decode("ascii", "ignore").strip("\x00 ")


def hhmm(v):
    """Charge period times are stored as (hour << 8) | minute."""
    return f"{v >> 8:02d}:{v & 0xFF:02d}"


class Metrics:
    def __init__(self):
        self.lines = []
        self.declared = set()

    def add(self, name, value, help_, type_="gauge", **labels):
        if name not in self.declared:
            self.lines.append(f"# HELP {name} {help_}")
            self.lines.append(f"# TYPE {name} {type_}")
            self.declared.add(name)
        lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
        self.lines.append(f"{name}{{{lbl}}} {value:g}" if lbl else f"{name} {value:g}")

    def render(self):
        return "\n".join(self.lines) + "\n"


_slow_cache = {"regs": {}, "read_at": 0.0}


def read_blocks(client, blocks):
    regs = {}
    for start, count in blocks:
        for i, v in enumerate(client.read(start, count)):
            regs[start + i] = v
    return regs


def collect(client):
    regs = read_blocks(client, FAST_BLOCKS)
    if time.monotonic() - _slow_cache["read_at"] > SLOW_INTERVAL:
        _slow_cache["regs"] = read_blocks(client, SLOW_BLOCKS)
        _slow_cache["read_at"] = time.monotonic()
    regs.update(_slow_cache["regs"])

    r = regs.__getitem__
    m = Metrics()

    m.add("foxess_info", 1, "Inverter information", model=string(r, 30000, 16), serial=string(r, 30016, 8),
          master_version=f"{r(36001) / 100:.2f}", slave_version=f"{r(36002) / 100:.2f}",
          manager_version=f"{r(36003) / 100:.2f}", protocol_version=f"{r(39000) >> 8}.{r(39000) & 0xFF:02x}")
    m.add("foxess_rated_power_watts", i32(r(39053), r(39054)), "Inverter rated power")
    m.add("foxess_max_apparent_power_va", i32(r(39057), r(39058)), "Inverter maximum apparent power")

    # Solar
    for n, base in ((1, 39070), (2, 39072)):
        m.add("foxess_pv_voltage_volts", r(base) * 0.1, "PV string voltage", string=n)
        m.add("foxess_pv_current_amps", s16(r(base + 1)) * 0.01, "PV string current", string=n)
    for n, base in ((1, 39279), (2, 39281)):
        m.add("foxess_pv_power_watts", max(i32(r(base), r(base + 1)), 0), "PV string power", string=n)

    # Grid / inverter AC side
    m.add("foxess_grid_voltage_volts", r(31006) * 0.1, "Grid voltage")
    m.add("foxess_grid_frequency_hertz", r(31009) * 0.01, "Grid frequency")
    m.add("foxess_inverter_current_amps", s16(r(31007)) * 0.1, "Inverter AC current")
    m.add("foxess_inverter_power_watts", s16(r(31008)), "Inverter AC power")
    m.add("foxess_grid_ct_power_watts", s16(r(31014)),
          "Grid meter power, positive = exporting, negative = importing")
    m.add("foxess_ct2_power_watts", s16(r(31015)), "CT2 meter power")
    m.add("foxess_load_power_watts", s16(r(31016)), "House load power")
    m.add("foxess_eps_voltage_volts", r(31010) * 0.1, "EPS (backup) output voltage")
    m.add("foxess_eps_current_amps", s16(r(31011)) * 0.1, "EPS (backup) output current")
    m.add("foxess_eps_power_watts", s16(r(31012)), "EPS (backup) output power")
    m.add("foxess_eps_frequency_hertz", r(31013) * 0.01, "EPS (backup) output frequency")

    # Inverter health
    m.add("foxess_inverter_temperature_celsius", s16(r(31018)) * 0.1, "Inverter temperature")
    m.add("foxess_ambient_temperature_celsius", s16(r(31019)) * 0.1, "Ambient temperature")
    status1, status3 = r(39063), r(39065)
    for state, on in (("standby", status1 & 0x01), ("operation", status1 & 0x04),
                      ("fault", status1 & 0x40), ("off_grid", status3 & 0x01)):
        m.add("foxess_inverter_state", 1 if on else 0, "Inverter state flags", state=state)
    for i in range(3):
        m.add("foxess_fault_code", r(39067 + i), "Raw fault bitmask registers", register=39067 + i)
    for reg, bits in ALARMS.items():
        for bit, name in bits.items():
            m.add("foxess_alarm", (r(reg) >> bit) & 1, "Inverter alarm active (1) or clear (0)", alarm=name)
    for n, reg in (("1", 38801), ("2", 38901)):
        m.add("foxess_meter_connected", r(reg), "Whether the grid CT meter is connected", meter=n)
    m.add("foxess_remote_control_enabled", r(44000) & 1,
          "Whether remote control (external charge/discharge commands) is enabled")

    # Battery
    m.add("foxess_battery_soc_percent", r(31024), "Battery state of charge")
    m.add("foxess_battery_soh_percent", r(37624), "Battery state of health")
    m.add("foxess_battery_voltage_volts", r(31020) * 0.1, "Battery voltage (inverter side)")
    m.add("foxess_battery_current_amps", s16(r(31021)) * 0.1, "Battery current (inverter side)")
    m.add("foxess_battery_power_watts", s16(r(31022)),
          "Battery power, positive = discharging, negative = charging")
    m.add("foxess_battery_temperature_celsius", s16(r(31023)) * 0.1, "Battery temperature")
    m.add("foxess_bms_voltage_volts", r(37609) * 0.1, "Battery voltage (BMS)")
    m.add("foxess_bms_current_amps", s16(r(37610)) * 0.1, "Battery current (BMS)")
    m.add("foxess_bms_cell_temperature_celsius", s16(r(37617)) * 0.1, "BMS cell temperature", bound="high")
    m.add("foxess_bms_cell_temperature_celsius", s16(r(37618)) * 0.1, "BMS cell temperature", bound="low")
    m.add("foxess_bms_charge_limit_amps", r(31025) * 0.1, "BMS max charge current")
    m.add("foxess_bms_discharge_limit_amps", r(31026) * 0.1, "BMS max discharge current")
    m.add("foxess_bms_connect_state", r(31028), "BMS connection state (1 = connected)")
    # The protocol documents these as mV, but this firmware reports 0.1 V steps.
    m.add("foxess_bms_cell_voltage_volts", r(37619) * 0.1, "BMS cell voltage", bound="high")
    m.add("foxess_bms_cell_voltage_volts", r(37620) * 0.1, "BMS cell voltage", bound="low")
    # 37632 is "remain energy" in the protocol, but it reads the same as the design energy regardless of SoC,
    # so it's the battery's current full capacity rather than what's stored.
    capacity = r(37632) * 0.01
    m.add("foxess_battery_capacity_kwh", capacity, "Battery usable capacity when full")
    m.add("foxess_battery_design_capacity_kwh", r(37635) * 0.01, "Battery capacity when new")
    m.add("foxess_battery_capacity_amp_hours", r(37633) * 0.1, "Battery full-charge capacity")
    m.add("foxess_battery_stored_kwh", capacity * r(31024) / 100, "Energy currently stored (capacity x SoC)")
    modules = r(37032)
    m.add("foxess_battery_modules", modules, "Number of battery modules")
    m.add("foxess_bms_info", 1, "Battery management system information", master_version=str(r(37003)),
          type=str(r(37004)), module_versions=",".join(str(r(37033 + i)) for i in range(min(modules, 9))))

    # Energy counters (0.1 kWh resolution; "today" values reset at inverter midnight)
    for name, total, today, help_ in (
        ("solar", 32000, 32002, "PV generation"),
        ("battery_charge", 32003, 32005, "Battery charge"),
        ("battery_discharge", 32006, 32008, "Battery discharge"),
        ("grid_export", 32009, 32011, "Energy exported to grid"),
        ("grid_import", 32012, 32014, "Energy imported from grid"),
        ("output", 32015, 32017, "Inverter AC output"),
        ("input", 32018, 32020, "Inverter AC input"),
        ("load", 32021, 32023, "House load"),
    ):
        m.add(f"foxess_{name}_energy_kwh_total", u32(r(total), r(total + 1)) * 0.1, f"{help_} (lifetime)",
              type_="counter")
        m.add(f"foxess_{name}_energy_today_kwh", r(today) * 0.1, f"{help_} (today)")

    # Settings
    m.add("foxess_work_mode", r(41000), "Work mode (0=self use, 1=feed-in first, 2=back-up)")
    m.add("foxess_min_soc_percent", r(41009), "Minimum SoC")
    m.add("foxess_max_soc_percent", r(41010), "Maximum SoC")
    m.add("foxess_min_soc_on_grid_percent", r(41011), "Minimum SoC on grid")
    m.add("foxess_export_limit_watts", r(41012), "Grid export limit")
    m.add("foxess_import_limit_watts", i32(r(46501), r(46502)), "Grid import limit")
    for n, base in (("1", 41001), ("2", 41004)):
        m.add("foxess_charge_period_enabled", r(base), "Forced grid-charge time period enabled",
              period=n, start=hhmm(r(base + 1)), end=hhmm(r(base + 2)))
    return m


class Handler(BaseHTTPRequestHandler):
    client = None

    def do_GET(self):
        if self.path == "/healthz":
            # Liveness only: doesn't touch the inverter (foxess_up covers that).
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path != "/metrics":
            self.send_response(302)
            self.send_header("Location", "/metrics")
            self.end_headers()
            return
        start = time.monotonic()
        try:
            m = collect(self.client)
            m.add("foxess_up", 1, "Whether the last inverter read succeeded")
        except Exception as e:  # report failure as a metric rather than a 500
            self.log_error("scrape failed: %r", e)
            m = Metrics()
            m.add("foxess_up", 0, "Whether the last inverter read succeeded")
        m.add("foxess_scrape_duration_seconds", time.monotonic() - start, "Time taken to read the inverter")
        body = m.render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        print(fmt % args, flush=True)


if __name__ == "__main__":
    Handler.client = ModbusClient(FOX_HOST, FOX_PORT, FOX_UNIT)
    print(f"foxess exporter: {FOX_HOST}:{FOX_PORT} unit {FOX_UNIT}, listening on :{LISTEN_PORT}", flush=True)
    ThreadingHTTPServer(("", LISTEN_PORT), Handler).serve_forever()
