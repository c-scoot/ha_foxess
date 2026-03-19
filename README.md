# FoxESS Cloud Home Assistant Integration

Custom Home Assistant integration for FoxESS systems using the official FoxESS cloud API.

## What it does

- Uses FoxESS API key authentication and signed requests.
- Supports setup through Home Assistant's GUI config flow.
- Auto-discovers every inverter on the FoxESS account and creates a Home Assistant device for each one.
- Creates power and energy entities for:
  - PV generation and per-string PV metrics
  - Grid import and feed-in
  - Home load
  - Battery charge, discharge, SOC, temperatures, and related metrics
- Separates instantaneous power, daily report totals, and cumulative/lifetime counters so similarly named FoxESS values are easier to interpret.
- Adds writable controls for battery reserve and force-charge periods when the inverter exposes those endpoints.
- Publishes daily energy counters suitable for Home Assistant long-term statistics and the Energy dashboard.

## Refresh strategy

The integration is aligned to the published FoxESS Open API limits:

- 1440 interface calls per inverter per day
- query interfaces limited to once per second per endpoint
- update interfaces limited to once every 2 seconds per endpoint

To stay comfortably within those limits, the integration uses:

- real-time polling every 5 minutes
- report polling every 5 minutes
- device detail refresh every 6 hours
- writable-settings refresh every 6 hours

The client also throttles requests per API path, so startup, multi-inverter accounts, and write operations respect the documented 1-second and 2-second endpoint limits.

FoxESS does not publish a per-variable cadence table in the Open API docs, but the real-time API does return a `time` field for each value. The integration carries that through as a `data_updated_at` attribute so you can see when FoxESS actually last refreshed a metric, rather than assuming every poll produced a new sample.

## Entity semantics

FoxESS exposes a few families of values that look similar but mean different things:

- `*_power` realtime sensors are instantaneous power values from the realtime endpoint, for example `Feed-in Power`, `Grid Consumption Power`, `Load Power`, and per-string PV power.
- Per-string `PV X Generated Energy` sensors are derived locally by integrating each `PV X Power` reading over time. They are intended for Home Assistant statistics and Energy dashboard use when you want to track strings individually.
- `daily_*` sensors come from the report endpoint and represent the current day's energy totals in the plant's timezone. These are the sensors intended for Home Assistant statistics and the Energy dashboard.
- Realtime `kWh` counters such as `Total Feed-in`, `Total Grid Consumption`, `Total Load Consumption`, `Total Battery Charged`, and `Total Battery Discharged` are cumulative counters from the realtime variable table, not daily totals.
- `feedin2` and `gridConsumption2` are the cumulative import/export counters for a secondary meter. They are not duplicates of the main grid counters, even though the names are similar.
- `Residual Energy` is current battery energy remaining, so it is an energy reading but not a monotonically increasing counter.

The official FoxESS variable table describes the cumulative realtime counters as totals:

- `gridConsumption`: total grid electricity consumption
- `gridConsumption2`: total electricity consumption of Meter 2
- `feedin`: total feed-in energy
- `feedin2`: total feed-in energy for Meter 2
- `loads`: load power consumption
- `chargeEnergyToTal`: total charge energy
- `dischargeEnergyToTal`: total discharge energy
- `energyThroughput`: battery throughput
- `PVEnergyTotal`: photovoltaic power generation

## Signing notes

FoxESS request signing is sensitive to the exact string used for the MD5 input. The integration signs requests using the documented path, token, and timestamp with literal `\r\n` separators, because FoxESS may reject otherwise-valid API keys as malformed requests if the signing format does not match exactly.

## Installation

Copy `custom_components/foxess_cloud` into your Home Assistant `custom_components` directory and restart Home Assistant.

## Setup

1. In FoxESS Cloud, generate an API key from your user profile.
2. In Home Assistant, go to `Settings -> Devices & services -> Add integration`.
3. Search for `FoxESS Cloud`.
4. Enter the API key. All inverters returned by the account will be added under one integration entry.

## Energy Dashboard

Recommended entity mapping:

- Solar production: `sensor.<device>_daily_pv_energy_total`
- Grid consumption: `sensor.<device>_daily_grid_consumption`
- Return to grid: `sensor.<device>_daily_feedin`
- Battery charged: `sensor.<device>_daily_battery_charged`
- Battery discharged: `sensor.<device>_daily_battery_discharged`

If your inverter/account does not expose `daily_pv_energy_total`, use `daily_generation` as the solar production fallback.

For individual PV strings, use the new derived entities such as `sensor.<device>_pv_1_generated_energy` and `sensor.<device>_pv_2_generated_energy` as separate solar sources.

`daily_pv_energy_total` is preferred for solar production because FoxESS added `PVEnergyTotal` specifically as PV generation data in the report API. `generation` appears to be the broader inverter generation/yield metric. That part is an inference from the official changelog and endpoint naming rather than an explicit FoxESS note.

Do not use the realtime cumulative `Total ...` counters in the Energy dashboard when a `daily_*` report sensor is available; they are different classes of data.

## API compatibility notes

The integration uses the request shapes documented in the official Open API:

- `GET /op/v1/device/detail` and `GET /op/v0/device/detail` with query parameter `sn`
- `POST /op/v1/device/real/query` with `sns: ["<serial>"]`
- `POST /op/v0/device/real/query` with `sn: "<serial>"`

## Writable controls

When supported by your inverter, the integration exposes:

- `number` entities for `Minimum SOC` and `Cut-Off SOC`
- `select` entity for `Work Mode`, limited to `Self-use` and `Mode Scheduler`
- `switch` entities for `Charge From Grid Period 1` and `Charge From Grid Period 2`
- `time` entities to edit the start/end time for each force-charge window

The force-charge controls map to FoxESS' basic battery charge-period settings:

- `Charge From Grid Period X` controls whether the inverter is allowed to draw from the grid during that window.
- `Force Charge Window X Start` and `End` define the time window for that period.
- `Cut-Off SOC` is the FoxESS grid-connected battery reserve value returned by the API as `minSocOnGrid`.

These controls are not the same thing as FoxCloud 2.0 `Mode Scheduler`. The official FoxCloud 2.0 app manual describes `Mode Scheduler` as a separate feature and notes that battery quick settings cannot be changed while `Mode Scheduler` is enabled. This integration currently manages the battery charge-period settings exposed by the Open API, not the full Mode Scheduler schedule editor.

In practice, FoxESS behavior appears to vary by inverter model and firmware. If your charge windows do not take effect immediately, check the active work mode in FoxESS and verify the corresponding `Charge From Grid Period` switch is on.

For the `0.0.2` release, the intended workflow is:

- configure your actual scheduler periods in the FoxESS app
- use the Home Assistant `Work Mode` select to switch between `Self-use` and `Mode Scheduler`

This keeps Home Assistant focused on arming or disarming the scheduler without trying to replicate FoxESS' full schedule editor.
When FoxESS exposes the current `WorkMode` setting for your inverter, the Home Assistant select will refresh from the cloud on the normal polling cycle so changes made in the FoxESS app are reflected back in HA.

Advanced users can also call Home Assistant services:

- `foxess_cloud.set_min_soc`
- `foxess_cloud.set_charge_periods`
- `foxess_cloud.set_device_setting`
