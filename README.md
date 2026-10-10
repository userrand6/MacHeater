# Mac Heater (Vibe Coded)

A macOS menu bar utility that generates CPU and GPU load to warm up your MacBook, with a target temperature, a heating duration, and safety cutoffs.

## Install

### With Homebrew (recommended)

```bash
brew install --cask userrand6/tap/mac-heater
```

This adds the tap automatically. If Homebrew asks you to trust the tap, run `brew trust --tap userrand6/tap` and retry.

The app is not notarized by Apple, so macOS blocks it on first launch. Either right-click `MacHeater.app` in Applications and choose **Open**, or allow it under **System Settings → Privacy & Security → Open Anyway**. You can also clear the quarantine flag yourself:

```bash
xattr -dr com.apple.quarantine /Applications/MacHeater.app
```

The release builds are for Apple Silicon (M1 and later). On an Intel Mac, [run from source](#run-from-source).

### From a release

Download `MacHeater-<version>.zip` from the [Releases](../../releases) page, unzip it, and move `MacHeater.app` to Applications. The first-launch steps above apply.

## Features

- **Temperature-regulated:** burns CPU below the target and idles at or above it, instead of running flat out.
- **Heating duration:** heating stops after the chosen time (5 min to 1 hour, default 10 min), counted from the start, whether or not the target temperature was reached.
- **Target temperature:** 20-35 °C in 2.5 °C steps (default 30 °C).
- **Adjustable load:** use Off or 25-100% of your CPU cores (default 25%). Workers run at low priority, so the menu bar stays responsive.
- **GPU load (optional):** a Metal compute kernel keeps the GPU busy at 25-100% duty, or Off (the default). It runs in short batches, so the system stays responsive. GPU heat is a good way to out-heat the fans.
- **Power checks:** won't start on battery unless you enable the manual override, which expires after 30 minutes.
- **Safety cutoffs:** stops on overheat (43 °C), on low battery (below 20% when unplugged), on sensor loss, and after 90 minutes.
- **Status in the menu:** the dropdown shows the current status, the time remaining, and why heating last stopped, with the time. The last-stop note is saved and survives restarts.
- **Remembers settings** in `~/.mac_heater_prefs.json`. The defaults above only apply until you choose something; your choices are saved and win. If both CPU and GPU are Off there is nothing to run, so the app refuses to start.

## How it measures temperature

The app reads the **battery temperature** (`VirtualTemperature` from `AppleSmartBattery` via `ioreg`, falling back to `Temperature`; on one test Mac the two differed by about 6 °C, and `VirtualTemperature` was the one that matched the Stats app) and the power state (`pmset`). Both work as a normal user, so **no `sudo` or admin rights are needed**.

This is a proxy for how warm the chassis is, not a trackpad sensor. It lags behind the surface temperature you actually feel, so try a short run first. The target temperatures are battery temperatures.

## Requirements

- A MacBook running macOS (Apple Silicon M1 and later, or Intel). Desktop Macs have no battery sensor, so the app can't read a temperature there.
- Python 3.9 or newer (only to run from source or build it yourself)

Apple Silicon and Intel use the same code path: the battery temperature keys are tried in order (`VirtualTemperature`, then `Temperature`), and the Metal GPU load works on both. On an Intel MacBook with two GPUs, the load runs on the default Metal device. Fanless MacBook Airs get hot quickly, so start with a low CPU setting. I've only checked the sensor output from one Apple Silicon Mac; other models are untested.

## Run from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python mac_heater.py
```

A ♨️ icon appears in the menu bar. Choose your target temperature, heating duration and CPU usage, then click **Start Heating**.

`requirements.txt` pins `setuptools<81` because py2app on Python 3.9 needs `pkg_resources`, which newer setuptools versions no longer ship.

## Build the app yourself (py2app)

The repo contains a ready `setup.py` and `icon.icns`.

```bash
python setup.py py2app
open dist/MacHeater.app
```

Add `-A` for a fast alias build while developing, and `rm -rf build dist` to start clean. Change `CFBundleIdentifier` in `setup.py` to your own identifier before publishing.

## Releasing (GitHub Actions)

`.github/workflows/release.yml` builds, signs ad hoc, zips and publishes the app. It's free: GitHub Actions is free for public repositories, including the macOS runners.

1. Make sure `setup.py`, `icon.icns`, `requirements.txt` and the workflow are committed.
2. Tag and push:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
3. The workflow builds on an Apple Silicon runner and creates a GitHub release with `MacHeater-1.0.0.zip` and its `.sha256` file.

To test the build without releasing, start the workflow manually from the **Actions** tab. It then uploads the zip as a workflow artifact only.

The ad-hoc signature lets the app launch on Apple Silicon, but it is not an Apple Developer ID signature, so users still see the first-launch prompt described above. Notarizing needs a paid Apple Developer account and can be added to the workflow later.

## Publishing through your own Homebrew tap

Official `homebrew-cask` has high notability thresholds for self-submitted projects, so a personal tap is the practical route.

1. **Create the tap repo** (once). The repository must be named `homebrew-tap`:
   ```bash
   brew tap-new userrand6/homebrew-tap
   gh repo create userrand6/homebrew-tap --push --public \
     --source "$(brew --repository userrand6/homebrew-tap)"
   ```
2. **Add the cask** as `Casks/mac-heater.rb` in that repo:
   ```ruby
   cask "mac-heater" do
     version "1.0.0"
     sha256 "<contents of MacHeater-1.0.0.zip.sha256>"

     url "https://github.com/userrand6/<this-repo>/releases/download/v#{version}/MacHeater-#{version}.zip"
     name "Mac Heater"
     desc "Menu bar utility that warms up your MacBook with CPU and GPU load"
     homepage "https://github.com/userrand6/<this-repo>"

     depends_on arch: :arm64

     livecheck do
       url :url
       strategy :github_latest
     end

     app "MacHeater.app"

     zap trash: "~/.mac_heater_prefs.json"
   end
   ```
3. **Commit and push**, then test:
   ```bash
   brew install --cask userrand6/tap/mac-heater
   ```
4. **For each new release:** after the workflow finishes, change `version` and `sha256` in the cask, commit and push.

`depends_on arch: :arm64` matches the Apple Silicon build. For Intel support, build on an Intel runner too and publish a second zip.

## Troubleshooting

| Problem | Fix |
|---|---|
| macOS says the app can't be opened | The app isn't notarized. Right-click → **Open**, or use **Open Anyway** in Privacy & Security. |
| "Sensor Error" notification | `ioreg` returned no battery temperature. This can happen on desktop Macs, which have no battery. |
| "Cannot Start: MacBook is unplugged" | Plug in, or enable **Manual Battery Override** (auto-off after 30 min). |
| No notifications appear | Allow notifications for the app (or your terminal, if running from source) in System Settings. |
| "GPU Usage: unavailable" in the menu | Metal bindings are missing or no GPU was found. Run `pip install pyobjc-framework-Metal`. |
| "GPU Worker Stopped" notification | The Metal kernel failed to start. Heating continues on CPU only. |
| Fans are loud or the Mac gets hot | Lower the target temperature or CPU usage, or stop heating. |
| Heating continues after a crash | Quit via the menu or run `pkill -f mac_heater`. Workers should exit on their own when the parent dies. |
| `No module named 'pkg_resources'` while building | Make sure `setuptools<81` is installed: `pip install -r requirements.txt`. |

## Notes

- Sustained full CPU load shortens battery life and adds thermal stress. Use it in moderation and keep the vents clear.
- CPU 100% plus GPU 100% can draw more power than a small charger supplies, so the battery may still drain while plugged in. Lower the load if the battery percentage drops.
- Settings migrated from older versions: the CPU preference key changed to `cpu_pct`, so that setting resets once.

## License

[MIT](LICENSE). Mac Heater deliberately keeps your CPU and GPU under sustained load. Use it at your own risk: the software is provided without warranty, as described in the license.