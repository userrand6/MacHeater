import rumps
import subprocess
import json
import os
import time
import multiprocessing

CONFIG_FILE = os.path.expanduser("~/.mac_heater_prefs.json")

def calculate_pi_chunk(stop_flag):
    """Generates pure CPU load by calculating Pi using the Leibniz formula."""
    while not stop_flag.value:
        k = 1
        s = 0
        for i in range(100000):
            if i % 2 == 0:
                s += 4/k
            else:
                s -= 4/k
            k += 2

def get_power_plugged_in():
    try:
        out = subprocess.check_output(["pmset", "-g", "batt"]).decode()
        return "AC Power" in out
    except Exception:
        return False

def get_temperatures():
    pad_temp = 0.0
    try:
        cmd = ["ioreg", "-r", "-n", "AppleSmartBattery"]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if '"Temperature" =' in line:
                val = int(line.split('=')[1].strip())
                pad_temp = val / 100.0
                break
    except Exception:
        pass
    return pad_temp

class MacHeaterApp(rumps.App):
    def __init__(self):
        super(MacHeaterApp, self).__init__("♨️", quit_button="Quit")
        
        # State
        self.is_running = False
        self.target_temp = 35.0
        self.duration_mins = 10
        self.override_power = False
        self.override_start_time = 0  # Tracks when override was enabled
        self.cpu_levels = [25, 50, 75, 100]
        self.cpu_level_idx = 3 # Default to 100%
        
        self.load_prefs()
        
        self.target_reached_time = 0
        self.heating_start_time = 0
        self.processes = []
        self.stop_flag = multiprocessing.Value('b', False)

        # Menu Structure
        self.status_menu = rumps.MenuItem("Status: Idle")
        self.start_stop_btn = rumps.MenuItem("Start Heating", callback=self.toggle_heating)
        
        # Settings Submenu items
        self.temp_menu = rumps.MenuItem("Target Temperature")
        self.temp_options = {}
        for t in [30.0, 32.5, 35.0, 37.5, 40.0]:
            item = rumps.MenuItem(f"{t}°C", callback=self.set_temp_target)
            if t == self.target_temp:
                item.state = 1
            self.temp_options[t] = item
            self.temp_menu.add(item)

        self.duration_menu = rumps.MenuItem("Hold Duration")
        self.duration_options = {}
        for d in [5, 10, 15, 30]:
            item = rumps.MenuItem(f"{d} minutes", callback=self.set_duration_target)
            if d == self.duration_mins:
                item.state = 1
            self.duration_options[d] = item
            self.duration_menu.add(item)

        self.cpu_menu = rumps.MenuItem("CPU Usage")
        self.cpu_options = {}
        for pct in self.cpu_levels:
            item = rumps.MenuItem(f"{pct}%", callback=self.set_cpu_target)
            if pct == self.cpu_levels[self.cpu_level_idx]:
                item.state = 1
            self.cpu_options[pct] = item
            self.cpu_menu.add(item)

        self.override_btn = rumps.MenuItem(f"Manual Battery Override: {'ON' if self.override_power else 'OFF'}", callback=self.toggle_override)

        self.menu = [
            self.status_menu,
            None,
            self.start_stop_btn,
            None,
            self.temp_menu,
            self.duration_menu,
            self.cpu_menu,
            self.override_btn,
            None
        ]

    def load_prefs(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    prefs = json.load(f)
                    self.target_temp = prefs.get('target_temp', 35.0)
                    self.duration_mins = prefs.get('duration_mins', 10)
                    self.cpu_level_idx = prefs.get('cpu_level_idx', 3)
            except Exception:
                pass

    def save_prefs(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump({
                'target_temp': self.target_temp,
                'duration_mins': self.duration_mins,
                'cpu_level_idx': self.cpu_level_idx
            }, f)

    def set_temp_target(self, sender):
        for t, item in self.temp_options.items():
            item.state = 0
        sender.state = 1
        self.target_temp = float(sender.title.replace('°C', ''))
        self.save_prefs()

    def set_duration_target(self, sender):
        for d, item in self.duration_options.items():
            item.state = 0
        sender.state = 1
        self.duration_mins = int(sender.title.split()[0])
        self.save_prefs()

    def set_cpu_target(self, sender):
        for pct, item in self.cpu_options.items():
            item.state = 0
        sender.state = 1
        pct_val = int(sender.title.replace('%', ''))
        self.cpu_level_idx = self.cpu_levels.index(pct_val)
        self.save_prefs()
        
        if self.is_running:
            self.stop_worker_processes()
            self.start_worker_processes()

    def toggle_override(self, _):
        self.override_power = not self.override_power
        if self.override_power:
            self.override_start_time = time.time()
        self.override_btn.title = f"Manual Battery Override: {'ON' if self.override_power else 'OFF'}"

    def start_worker_processes(self):
        self.stop_flag.value = False
        total_cores = multiprocessing.cpu_count()
        pct = self.cpu_levels[self.cpu_level_idx]
        active_cores = max(1, int(total_cores * (pct / 100.0)))
        
        for _ in range(active_cores):
            p = multiprocessing.Process(target=calculate_pi_chunk, args=(self.stop_flag,))
            p.start()
            self.processes.append(p)

    def stop_worker_processes(self):
        self.stop_flag.value = True
        for p in self.processes:
            p.join(timeout=1)
            if p.is_alive():
                p.terminate()
        self.processes.clear()

    def toggle_heating(self, _):
        if self.is_running:
            self.stop_heating()
        else:
            self.start_heating()

    def start_heating(self):
        pad_temp = get_temperatures()
        
        if pad_temp <= 0.0:
            rumps.notification("Sensor Error", "Cannot read trackpad temperature.", "Sensor validation failed.")
            return

        if not get_power_plugged_in() and not self.override_power:
            rumps.notification("Cannot Start", "MacBook is unplugged.", "Enable Manual Override to run on battery.")
            return

        self.is_running = True
        self.target_reached_time = 0
        self.heating_start_time = time.time()
        
        self.start_worker_processes()
        self.title = "♨️ (Heating)"

    def stop_heating(self):
        self.is_running = False
        self.start_stop_btn.title = "Start Heating"
        self.title = "♨️"
        
        self.stop_worker_processes()
        self.status_menu.title = "Status: Idle"

    @rumps.timer(3)
    def monitor(self, _):
        pad_temp = get_temperatures()
        plugged_in = get_power_plugged_in()
        temp_str = f"(Pad: {pad_temp}°C)"

        # Check if manual override needs to automatically turn off after 30 minutes
        if self.override_power:
            override_elapsed = (time.time() - self.override_start_time) / 60.0
            if override_elapsed >= 30.0:
                self.override_power = False
                self.override_btn.title = "Manual Battery Override: OFF"
                if self.is_running and not plugged_in:
                    self.stop_heating()
                rumps.notification("Override Expired", "Battery override turned off automatically.", "30-minute limit reached.")
        
        if self.is_running:
            if pad_temp <= 0.0:
                self.stop_heating()
                rumps.notification("Sensor Error", "Lost trackpad temperature reading.", "Heating aborted for safety.")
                return

            if not plugged_in and not self.override_power:
                self.stop_heating()
                rumps.notification("Power Lost", "Unplugged from AC and override expired/disabled.", "Heating stopped.")
                return

            if pad_temp >= self.target_temp:
                if self.target_reached_time == 0:
                    self.target_reached_time = time.time()
                
                elapsed_mins = (time.time() - self.target_reached_time) / 60.0
                self.status_menu.title = f"Status: Holding Target ({elapsed_mins:.1f}/{self.duration_mins}m) {temp_str}"
                
                if elapsed_mins >= self.duration_mins:
                    self.stop_heating()
                    rumps.notification("Finished", "Target temperature and duration met.", "Cooling down.")
            else:
                self.target_reached_time = 0
                self.status_menu.title = f"Status: Heating... {temp_str}"
        else:
            self.status_menu.title = f"Status: Idle {temp_str}"

if __name__ == "__main__":
    MacHeaterApp().run()