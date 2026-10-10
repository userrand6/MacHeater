import atexit
import json
import multiprocessing
import os
import re
import struct
import subprocess
import sys
import time

import rumps

try:  # GPU load needs PyObjC's Metal bindings (pip install pyobjc-framework-Metal)
    import objc
    import Metal
except ImportError:
    objc = Metal = None

CONFIG_FILE = os.path.expanduser("~/.mac_heater_prefs.json")

TEMP_CHOICES = [20.0, 22.5, 25.0, 27.5, 30.0, 32.5, 35.0]
DURATION_CHOICES = [5, 10, 15, 30, 45, 60]   # minutes
CPU_CHOICES = [0, 25, 50, 75, 100]           # 0 = off
GPU_CHOICES = [0, 25, 50, 75, 100]           # 0 = off

# Used until the user picks something; picks are saved in CONFIG_FILE and win.
DEFAULT_TEMP = 30.0
DEFAULT_DURATION = 10
DEFAULT_CPU = 25
DEFAULT_GPU = 0

# ioreg keys tried in order. VirtualTemperature tracked Stats' battery reading
# (23.4 vs 22 °C) on the author's Mac; plain Temperature read ~6 °C higher.
TEMP_KEYS = ("VirtualTemperature", "Temperature")

SAFETY_MAX_TEMP = 43.0      # hard cutoff (°C, battery sensor)
MIN_BATTERY_PCT = 20         # stop if running on battery below this
HYSTERESIS = 0.5             # resume burning below target - HYSTERESIS
OVERRIDE_LIMIT_MIN = 30
MAX_SESSION_MIN = 90         # absolute cap per heating session

GPU_BATCH_SECONDS = 0.05     # length of each GPU command buffer (keeps UI smooth, avoids GPU watchdog)
GPU_THREADGROUPS = 4096

GPU_KERNEL = """
#include <metal_stdlib>
using namespace metal;
kernel void burn(device float *out [[buffer(0)]],
                 constant uint &iters [[buffer(1)]],
                 uint gid [[thread_position_in_grid]]) {
    float x = float(gid) * 1e-3f + 0.5f;
    for (uint i = 0; i < iters; i++) {
        x = fma(x, 1.000001f, sin(x) * 1e-3f);
    }
    out[gid] = x;
}
"""


# --------------------------------------------------------------------------
# Workers
# --------------------------------------------------------------------------
def burn_cpu(stop_event, active_event, parent_pid):
    """Pure CPU load. Pauses when active_event is cleared, exits on stop_event
    or if the parent app disappears (so a crash can never leave orphans burning)."""
    try:
        os.nice(10)  # keep the UI and the rest of the system responsive
    except OSError:
        pass
    while not stop_event.is_set():
        if os.getppid() != parent_pid:
            return
        if not active_event.wait(0.5):
            continue
        s, k = 0.0, 1
        for i in range(200000):
            s += 4 / k if i % 2 == 0 else -4 / k
            k += 2


def burn_gpu(stop_event, active_event, parent_pid, duty_pct):
    """GPU load via a Metal compute kernel, in short batches. duty_pct sets the
    fraction of time the GPU is kept busy. Same pause/exit rules as burn_cpu."""
    if Metal is None or objc is None:
        return
    try:
        device = Metal.MTLCreateSystemDefaultDevice()
        if device is None:
            return
        queue = device.newCommandQueue()
        library, _err = device.newLibraryWithSource_options_error_(GPU_KERNEL, None, None)
        if library is None:
            return
        function = library.newFunctionWithName_("burn")
        pso, _err = device.newComputePipelineStateWithFunction_error_(function, None)
        if pso is None:
            return
        threads = min(256, int(pso.maxTotalThreadsPerThreadgroup()))
        buf = device.newBufferWithLength_options_(
            GPU_THREADGROUPS * threads * 4, Metal.MTLResourceStorageModeShared)
        groups = Metal.MTLSizeMake(GPU_THREADGROUPS, 1, 1)
        per_group = Metal.MTLSizeMake(threads, 1, 1)
    except Exception:
        return

    duty = max(1, min(100, duty_pct))
    iters = 256  # start tiny, then calibrate toward GPU_BATCH_SECONDS per batch
    while not stop_event.is_set():
        if os.getppid() != parent_pid:
            return
        if not active_event.wait(0.5):
            continue
        try:
            with objc.autorelease_pool():
                t0 = time.perf_counter()
                cmd = queue.commandBuffer()
                enc = cmd.computeCommandEncoder()
                enc.setComputePipelineState_(pso)
                enc.setBuffer_offset_atIndex_(buf, 0, 0)
                enc.setBytes_length_atIndex_(struct.pack("I", iters), 4, 1)
                enc.dispatchThreadgroups_threadsPerThreadgroup_(groups, per_group)
                enc.endEncoding()
                cmd.commit()
                cmd.waitUntilCompleted()
                busy = time.perf_counter() - t0
        except Exception:
            return
        if busy > 0:
            ratio = max(0.5, min(2.0, GPU_BATCH_SECONDS / busy))
            iters = int(max(16, min(1 << 20, iters * ratio)))
        if duty < 100:
            stop_event.wait(busy * (100 - duty) / duty)


class WorkerGroup:
    """A set of worker processes sharing one stop event. All groups share one
    active_event, which the monitor clears to pause everything at the target."""

    def __init__(self, target, active_event):
        self.target = target
        self.active = active_event
        self.stop = multiprocessing.Event()
        self.procs = []

    def start(self, count, *extra):
        self.stop.clear()
        for _ in range(count):
            p = multiprocessing.Process(
                target=self.target,
                args=(self.stop, self.active, os.getpid(), *extra),
                daemon=True)
            p.start()
            self.procs.append(p)

    def shutdown(self):
        was_active = self.active.is_set()
        self.stop.set()
        self.active.set()  # wake any paused workers so they can exit
        for p in self.procs:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()
        self.procs.clear()
        if not was_active:
            self.active.clear()  # don't un-pause the other group

    def alive(self):
        return any(p.is_alive() for p in self.procs)


# --------------------------------------------------------------------------
# System readings
# --------------------------------------------------------------------------
def gpu_supported():
    if Metal is None:
        return False
    try:
        return Metal.MTLCreateSystemDefaultDevice() is not None
    except Exception:
        return False


def get_power_state():
    """Returns (plugged_in, battery_percent_or_None)."""
    try:
        out = subprocess.check_output(["pmset", "-g", "batt"], timeout=5, text=True)
    except Exception:
        return False, None
    plugged = "'AC Power'" in out
    m = re.search(r"(\d+)%", out)
    return plugged, (int(m.group(1)) if m else None)


def get_battery_temp():
    """Battery temperature in °C, or None. NOTE: this is the battery sensor,
    not the trackpad; it is only a proxy for how warm the chassis is."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-r", "-n", "AppleSmartBattery"],
            stderr=subprocess.DEVNULL, timeout=5, text=True,
        )
    except Exception:
        return None
    for key in TEMP_KEYS:
        m = re.search(rf'"{key}"\s*=\s*(\d+)', out)
        if m:
            val = int(m.group(1)) / 100.0  # reported in 1/100 °C
            if 0.0 < val < 100.0:
                return val
    return None


def fmt_duration(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def notify(title, subtitle, message):
    try:
        rumps.notification(title, subtitle, message)
    except Exception:
        pass  # notifications can fail when run outside an app bundle


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class MacHeaterApp(rumps.App):
    def __init__(self):
        super().__init__("♨️", quit_button=None)

        self.is_running = False
        self.target_temp = DEFAULT_TEMP
        self.duration_mins = DEFAULT_DURATION
        self.cpu_pct = DEFAULT_CPU
        self.gpu_pct = DEFAULT_GPU
        self.override_power = False
        self.override_start_time = 0.0
        self.heating_start_time = 0.0
        self.reached_target = False
        self.gpu_failed = False
        self.last_stop = "none yet"

        self.load_prefs()

        self.gpu_ok = gpu_supported()
        if not self.gpu_ok:
            self.gpu_pct = 0

        self.active_event = multiprocessing.Event()
        self.cpu_group = WorkerGroup(burn_cpu, self.active_event)
        self.gpu_group = WorkerGroup(burn_gpu, self.active_event)
        atexit.register(self.stop_all_workers)

        self.status_menu = rumps.MenuItem("Status: Idle")
        self.start_stop_btn = rumps.MenuItem("Start Heating", callback=self.toggle_heating)
        self.temp_menu = self._radio_menu(
            "Target Temperature", TEMP_CHOICES, self.target_temp,
            lambda v: f"{v}°C", self.set_temp_target)
        self.duration_menu = self._radio_menu(
            "Heating Duration", DURATION_CHOICES, self.duration_mins,
            lambda v: "1 hour" if v == 60 else f"{v} minutes", self.set_duration_target)
        self.cpu_menu = self._radio_menu(
            "CPU Usage", CPU_CHOICES, self.cpu_pct,
            lambda v: "Off" if v == 0 else f"{v}%", self.set_cpu_target)
        if self.gpu_ok:
            self.gpu_menu = self._radio_menu(
                "GPU Usage", GPU_CHOICES, self.gpu_pct,
                lambda v: "Off" if v == 0 else f"{v}%", self.set_gpu_target)
        else:
            self.gpu_menu = rumps.MenuItem("GPU Usage: unavailable")  # no callback = greyed out
        self.override_btn = rumps.MenuItem(
            "Manual Battery Override: OFF", callback=self.toggle_override)

        self.remaining_menu = rumps.MenuItem("Time remaining: —")
        self.last_stop_menu = rumps.MenuItem(f"Last stop: {self.last_stop}")

        self.menu = [
            self.status_menu, self.remaining_menu, self.last_stop_menu, None,
            self.start_stop_btn, None,
            self.temp_menu, self.duration_menu, self.cpu_menu, self.gpu_menu,
            self.override_btn, None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

    # ---- menu helpers ----------------------------------------------------
    @staticmethod
    def _radio_menu(title, values, current, fmt, callback):
        menu = rumps.MenuItem(title)
        for v in values:
            item = rumps.MenuItem(fmt(v), callback=callback)
            item.value = v
            item.state = 1 if v == current else 0
            menu.add(item)
        return menu

    @staticmethod
    def _select(menu, sender):
        for item in menu.values():
            item.state = 0
        sender.state = 1

    # ---- prefs -----------------------------------------------------------
    def load_prefs(self):
        try:
            with open(CONFIG_FILE) as f:
                prefs = json.load(f)
        except Exception:
            return
        t = prefs.get("target_temp")
        d = prefs.get("duration_mins")
        c = prefs.get("cpu_pct")
        g = prefs.get("gpu_pct")
        if t in TEMP_CHOICES:
            self.target_temp = t
        if d in DURATION_CHOICES:
            self.duration_mins = d
        if c in CPU_CHOICES:
            self.cpu_pct = c
        if g in GPU_CHOICES:
            self.gpu_pct = g
        ls = prefs.get("last_stop")
        if isinstance(ls, str) and ls:
            self.last_stop = ls[:120]

    def save_prefs(self):
        tmp = CONFIG_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"target_temp": self.target_temp,
                           "duration_mins": self.duration_mins,
                           "cpu_pct": self.cpu_pct,
                           "gpu_pct": self.gpu_pct,
                           "last_stop": self.last_stop}, f)
            os.replace(tmp, CONFIG_FILE)  # atomic: no half-written prefs
        except OSError:
            pass

    # ---- settings callbacks ---------------------------------------------
    def set_temp_target(self, sender):
        self._select(self.temp_menu, sender)
        self.target_temp = sender.value
        self.save_prefs()

    def set_duration_target(self, sender):
        self._select(self.duration_menu, sender)
        self.duration_mins = sender.value
        self.save_prefs()

    def set_cpu_target(self, sender):
        self._select(self.cpu_menu, sender)
        self.cpu_pct = sender.value
        self.save_prefs()
        if self.is_running:
            self.cpu_group.shutdown()
            self.start_cpu_workers()
            if self._no_load():
                self.stop_heating(("Nothing to Run", "CPU and GPU usage are both off.", "Heating stopped."))

    def set_gpu_target(self, sender):
        self._select(self.gpu_menu, sender)
        self.gpu_pct = sender.value
        self.save_prefs()
        if self.is_running:
            self.gpu_group.shutdown()
            self.gpu_failed = False
            self.start_gpu_worker()
            if self._no_load():
                self.stop_heating(("Nothing to Run", "CPU and GPU usage are both off.", "Heating stopped."))

    def toggle_override(self, _):
        self.override_power = not self.override_power
        if self.override_power:
            self.override_start_time = time.time()
        self.override_btn.title = (
            f"Manual Battery Override: {'ON' if self.override_power else 'OFF'}")

    # ---- workers ---------------------------------------------------------
    def _no_load(self):
        """True when neither CPU nor GPU would run anything."""
        return self.cpu_pct == 0 and not (self.gpu_ok and self.gpu_pct > 0)

    def start_cpu_workers(self):
        if self.cpu_pct <= 0:
            return
        n = max(1, int(multiprocessing.cpu_count() * self.cpu_pct / 100.0))
        self.cpu_group.start(n)

    def start_gpu_worker(self):
        if self.gpu_ok and self.gpu_pct > 0:
            self.gpu_group.start(1, self.gpu_pct)

    def stop_all_workers(self):
        self.cpu_group.shutdown()
        self.gpu_group.shutdown()

    # ---- start / stop ----------------------------------------------------
    def toggle_heating(self, _):
        if self.is_running:
            self.stop_heating()
        else:
            self.start_heating()

    def start_heating(self):
        if self._no_load():
            notify("Nothing to Run", "CPU and GPU usage are both off.",
                   "Choose a CPU or GPU usage above 0%.")
            return
        temp = get_battery_temp()
        if temp is None:
            notify("Sensor Error", "Cannot read battery temperature.",
                   "This app needs a MacBook with a battery sensor.")
            return
        if temp >= SAFETY_MAX_TEMP:
            notify("Too Warm", f"Battery is already at {temp:.1f}°C.", "Let it cool down first.")
            return
        plugged, batt = get_power_state()
        if not plugged:
            if not self.override_power:
                notify("Cannot Start", "MacBook is unplugged.",
                       "Enable Manual Override to run on battery.")
                return
            if batt is not None and batt < MIN_BATTERY_PCT:
                notify("Battery Low", f"Battery at {batt}%.", "Plug in to start heating.")
                return

        self.is_running = True
        self.gpu_failed = False
        self.reached_target = False
        self.heating_start_time = time.time()
        self.active_event.set()
        self.start_cpu_workers()
        self.start_gpu_worker()
        self.start_stop_btn.title = "Stop Heating"
        self.title = "♨️ Heating"

    def _record_stop(self, label):
        self.last_stop = f"{time.strftime('%H:%M')} – {label}"
        self.last_stop_menu.title = f"Last stop: {self.last_stop}"
        self.save_prefs()

    def stop_heating(self, reason=None):
        """reason is a (title, subtitle, message) notification tuple; None = user stopped it."""
        self.is_running = False
        self.stop_all_workers()
        self.start_stop_btn.title = "Start Heating"
        self.title = "♨️"
        self.status_menu.title = "Status: Idle"
        self.remaining_menu.title = "Time remaining: —"
        if reason:
            self._record_stop(f"{reason[0]} – {reason[1].rstrip('.')}")
            notify(*reason)
        else:
            self._record_stop("Stopped manually")

    def quit_app(self, _):
        if self.is_running:
            self._record_stop("App quit while heating")
        self.stop_all_workers()
        rumps.quit_application()

    # ---- monitor ---------------------------------------------------------
    @rumps.timer(3)
    def monitor(self, _):
        temp = get_battery_temp()
        plugged, batt = get_power_state()
        now = time.time()
        temp_str = f"(Temp: {temp:.1f}°C)" if temp is not None else "(Temp: n/a)"

        # Battery override auto-expiry
        if self.override_power and (now - self.override_start_time) / 60.0 >= OVERRIDE_LIMIT_MIN:
            self.override_power = False
            self.override_btn.title = "Manual Battery Override: OFF"
            notify("Override Expired", "Battery override turned off.", "30-minute limit reached.")

        if not self.is_running:
            self.status_menu.title = f"Status: Idle {temp_str}"
            return

        # ---- safety checks ----
        if temp is None:
            return self.stop_heating(("Sensor Error", "Lost temperature reading.", "Heating aborted for safety."))
        if temp >= SAFETY_MAX_TEMP:
            return self.stop_heating(("Overheat Protection", f"Battery hit {temp:.1f}°C.", "Heating stopped."))
        if not plugged:
            if not self.override_power:
                return self.stop_heating(("Power Lost", "Unplugged and override is off.", "Heating stopped."))
            if batt is not None and batt < MIN_BATTERY_PCT:
                return self.stop_heating(("Battery Low", f"Battery at {batt}%.", "Heating stopped."))
        if (now - self.heating_start_time) / 60.0 >= MAX_SESSION_MIN:
            return self.stop_heating(("Session Limit", "Maximum session length reached.", "Heating stopped."))

        # GPU worker died on its own (Metal init failed, etc.): tell the user once, keep CPU heating
        if self.gpu_pct > 0 and not self.gpu_group.alive() and not self.gpu_failed:
            self.gpu_failed = True
            if self.cpu_pct == 0:
                return self.stop_heating(
                    ("GPU Worker Stopped", "GPU load could not run.", "CPU is off, so nothing is left to run."))
            notify("GPU Worker Stopped", "GPU load could not run.", "Continuing with CPU only.")

        # ---- regulation: burn below target, idle at/above it ----
        at_target = temp >= self.target_temp
        if at_target:
            self.reached_target = True
            self.active_event.clear()
        elif temp <= self.target_temp - HYSTERESIS:
            self.active_event.set()

        self.title = f"♨️ {temp:.0f}°"

        # ---- heating duration: counts from the start, target reached or not ----
        elapsed_s = now - self.heating_start_time
        left_s = self.duration_mins * 60 - elapsed_s
        state = "At target, idling" if at_target else "Heating"
        self.status_menu.title = (
            f"Status: {state} ({elapsed_s / 60.0:.1f}/{self.duration_mins}m) {temp_str}")
        self.remaining_menu.title = f"Time remaining: {fmt_duration(left_s)}"
        if left_s <= 0:
            reached = "target reached" if self.reached_target else "target not reached"
            self.stop_heating(("Finished", f"Heating duration done, {reached}.", "Cooling down."))


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("Mac Heater only runs on macOS.")
    multiprocessing.freeze_support()
    MacHeaterApp().run()