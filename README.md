# Mac Heater

A macOS menu bar utility that generates CPU load to warm up your MacBook, with a target temperature, a heating duration, and safety cutoffs.

## Features

- **Temperature-regulated:** burns CPU below the target and idles at or above it, instead of running flat out.
- **Heating duration:** heating stops after the chosen time (5 min to 1 hour, default 10 min), counted from the start, whether or not the target temperature was reached.
- **Target temperature:** 20-35 °C in 2.5 °C steps (default 30 °C).
- **Adjustable load:** use Off or 25-100% of your CPU cores (default 25%). Workers run at low priority, so the menu bar stays responsive.
- **GPU load (optional):** a Metal compute kernel keeps the GPU busy at 25-100% duty, or Off (the default). It runs in short batches, so the system stays responsive. GPU heat is a good way to out-heat the fans.
- **Power checks:** won't start on battery unless you enable the manual override, which expires after 30 minutes.
- **Safety cutoffs:** stops on overheat (43 °C), on low battery (below 20% when unplugged), on sensor loss, and after 90 minutes.
- **Status in the menu:** the dropdown shows the current status, the time remaining (time left of the heating duration), and why heating last stopped, with the time. The last-stop note is saved and survives restarts.
- **Remembers settings** in `~/.mac_heater_prefs.json`. The defaults above only apply until you choose something; your choices are saved and win. If both CPU and GPU are Off there is nothing to run, so the app refuses to start.

## How it measures temperature

The app reads the **battery temperature** (`VirtualTemperature` from `AppleSmartBattery` via `ioreg`, falling back to `Temperature`; on one test Mac the two differed by about 6 °C, and `VirtualTemperature` was the one that matched the Stats app) and the power state (`pmset`). Both work as a normal user, so **no `sudo` or admin rights are needed**.

This is a proxy for how warm the chassis is, not a trackpad sensor. It lags behind the surface temperature you actually feel, so try a short run first. The target temperatures are battery temperatures.

## Requirements

- A MacBook running macOS (Apple Silicon M1 and later, or Intel). Desktop Macs have no battery sensor, so the app can't read a temperature there.
- Python 3.9 or newer

Apple Silicon and Intel use the same code path: the battery temperature keys are tried in order (`VirtualTemperature`, then `Temperature`), and the Metal GPU load works on both. On an Intel MacBook with two GPUs, the load runs on the default Metal device. Fanless MacBook Airs get hot quickly, so start with a low CPU setting. I've only checked the sensor output from one Apple Silicon Mac; other models are untested.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Run from source

```bash
python mac_heater.py
```

A ♨️ icon appears in the menu bar. Choose your target temperature, heating duration and CPU usage, then click **Start Heating**.

## Package as a standalone app (py2app)


### 1. Build

```bash
python setup.py py2app
```

This creates `dist/MacHeater.app`. Add `-A` for a fast alias build while developing.

### 2. Run and clean up

```bash
open dist/MacHeater.app
rm -rf build   # optional
```

Because the app is unsigned, macOS may block the first launch. Right-click the app, choose **Open**, and confirm.

## Notes

- Sustained full CPU load shortens battery life and adds thermal stress. Use it in moderation and keep the vents clear.
- CPU 100% plus GPU 100% can draw more power than a small charger supplies, so the battery may still drain while plugged in. Lower the load if the battery percentage drops.
