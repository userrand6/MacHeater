

# Mac Heater Menu Bar App

A macOS menu bar utility that safely generates CPU load to warm up your MacBook's chassis (trackpad area), complete with target temperature monitoring, safety cutoffs, and power state detection.

## Einrichtung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

```

*(Ensure your `requirements.txt` includes both `rumps` and `py2app`)*.

## Packaging as a Standalone App (py2app)

To convert the Python script into a native macOS `.app` bundle, use `py2app`.

### 1. Create the Setup File

Generate the basic `setup.py` configuration:

```bash
py2applet --make-setup mac_heater.py

```

### 2. Build the App

Run the build script. This will create a `dist` folder containing `MacHeater.app`.

```bash
# Build the standalone application
python setup.py py2app

```

### 3. Cleanup

Once the build is successful, you can optionally clean up the temporary build directories:

```bash
rm -rf build

```

---

## ⚠️ Important: Running with Admin Privileges

Because this app relies on the macOS `powermetrics` command to read the CPU and battery (trackpad) temperatures, **it requires `sudo` (root) privileges to function correctly**.

If you simply double-click the newly created `MacHeater.app` in Finder, it will launch, but it will fail to read temperatures and stay at `0.0°C`.

**How to run your packaged app:**

**Option A: Run via Terminal (Recommended for testing)**
Execute the inner binary of the app bundle with `sudo`:

```bash
sudo ./dist/mac_heater.app/Contents/MacOS/mac_heater

```

**Option B: Allow passwordless powermetrics (For seamless GUI double-clicking)**
If you want to be able to just double-click the `.app` icon without it failing, you must tell macOS not to require a password for the `powermetrics` command.

1. Open terminal and edit the sudoers file: `sudo visudo`
2. Add this line to the very bottom (replace `YOUR_USERNAME` with your actual Mac username):
```text
YOUR_USERNAME ALL=(ALL) NOPASSWD: /usr/bin/powermetrics

```


3. Update `mac_heater.py` to call `sudo powermetrics` instead of just `powermetrics`:
```python
# Change this line inside get_temperatures():
cmd = ["sudo", "powermetrics", "-n", "1", "--samplers", "smc"]

```


4. Rebuild the app with `py2app`. You can now double-click it normally.