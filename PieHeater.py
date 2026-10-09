import rumps
import subprocess
import json
import os
import time
import multiprocessing
import math

CONFIG_FILE = os.path.expanduser("~/.mac_heater_prefs.json")

def calculate_pi_chunk(stop_flag):
    """Generates pure CPU load by calculating Pi using the Leibniz formula."""
    while not stop_flag.value:
        k = 1
        s = 0
        # Inner loop to prevent constant checking of the stop_flag
        for i in range(100000):
            if i % 2 == 0:
                s += 4/k
            else:
                s -= 4/k
            k += 2

def get_power_plugged_in():
    """Returns True if connected to AC power."""
    try:
        out = subprocess.check_output(["pmset", "-g", "batt"]).decode()
        return "AC Power" in out
    except Exception:
        return False

def get_temperatures():
    """
    Reads CPU and Battery (Trackpad proxy) temperatures.
    Requires sudo to run powermetrics.
    """
    try:
        cmd = ["powermetrics", "-n", "1", "--samplers", "smc"]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
        cpu_temp = 0.0
        pad_temp = 0.0
        for line in output.split('\n'):
            if "CPU die temperature" in line:
                cpu_temp = float(line.split(':')[1].strip().replace(' C', ''))
            elif "Battery temperature" in line: # Proxy for trackpad chassis
                pad_temp = float(line.split(':')[1].strip().replace(' C', ''))
        return cpu_temp, pad_temp
    except Exception:
        return 0.0, 0.0

class MacHeaterApp(rumps.App):
    def __init__(self):
        super(MacHeaterApp, self).__init__("♨️", quit_button="Quit")
        
        self.is_running = False
        self.target_temp = 25.0 # Celsius
        self.duration_mins = 10
        self.override_power = False
        
        self.load_prefs()
        
        self.target_reached_time = 0
        self.processes = []
        self.stop_flag = multiprocessing.Value('b', False)

        # Menu Items
        self.status_menu = rumps.MenuItem("Status: Idle")
        self.start_stop_btn = rumps.MenuItem("Start Heating", callback=self.toggle_heating)
        
        self.temp_btn = rumps.MenuItem(f"Set Target Temp (Current: {self.target_temp}°C)", callback=self.set_temp)
        self.duration_btn = rumps.MenuItem(f"Set Duration (Current: {self.duration_mins}m)", callback=self.set_duration)
        self.override_btn = rumps.MenuItem("Manual Battery Override: OFF", callback=self.toggle_override)
        
        self.menu = [
            self.status_menu,
            None,
            self.start_stop_btn,
            self.temp_btn,
            self.duration_btn,
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
            except Exception:
                pass

    def save_prefs(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump({
                'target_temp': self.target_temp,
                'duration_mins': self.duration_mins
            }, f)

    def set_temp(self, _):
        window = rumps.Window(message="Enter target trackpad/battery temp in °C:", title="Target Temperature", default_text=str(self.target_temp))
        response = window.run()
        if response.clicked:
            try:
                self.target_temp = float(response.text)
                self.temp_btn.title = f"Set Target Temp (Current: {self.target_temp}°C)"
                self.save_prefs()
            except ValueError:
                rumps.notification("Invalid Input", "Please enter a valid number.", "")

    def set_duration(self, _):
        window = rumps.Window(message="Enter duration in minutes to hold temp:", title="Hold Duration", default_text=str(self.duration_mins))
        response = window.run()
        if response.clicked:
            try:
                self.duration_mins = float(response.text)
                self.duration_btn.title = f"Set Duration (Current: {self.duration_mins}m)"
                self.save_prefs()
            except ValueError:
                rumps.notification("Invalid Input", "Please enter a valid number.", "")

    def toggle_override(self, _):
        self.override_power = not self.override_power
        self.override_btn.title = f"Manual Battery Override: {'ON' if self.override_power else 'OFF'}"

    def toggle_heating(self, _):
        if self.is_running:
            self.stop_heating()
        else:
            self.start_heating()

    def start_heating(self):
        if not get_power_plugged_in() and not self.override_power:
            rumps.notification("Cannot Start", "MacBook is unplugged.", "Enable Manual Override to run on battery.")
            return

        self.is_running = True
        self.start_stop_btn.title = "Stop Heating"
        self.target_reached_time = 0
        self.stop_flag.value = False
        
        # Spawn processes matching CPU core count
        cores = multiprocessing.cpu_count()
        for _ in range(cores):
            p = multiprocessing.Process(target=calculate_pi_chunk, args=(self.stop_flag,))
            p.start()
            self.processes.append(p)
            
        self.title = "♨️ (Heating)"

    def stop_heating(self):
        self.is_running = False
        self.start_stop_btn.title = "Start Heating"
        self.title = "♨️"
        self.stop_flag.value = True
        
        for p in self.processes:
            p.join(timeout=1)
            if p.is_alive():
                p.terminate()
        self.processes.clear()
        self.status_menu.title = "Status: Idle"

    @rumps.timer(3)
    def monitor(self, _):
        cpu_temp, pad_temp = get_temperatures()
        plugged_in = get_power_plugged_in()
        
        if self.is_running:
            # 1. Safety Cutoff
            if cpu_temp > 80.0:
                self.stop_heating()
                rumps.notification("Safety Stop", "CPU/GPU Exceeded 80°C", "Heating aborted to protect hardware.")
                return

            # 2. Power Unplugged Cutoff
            if not plugged_in and not self.override_power:
                self.stop_heating()
                rumps.notification("Power Lost", "Unplugged from AC.", "Heating stopped.")
                return

            # 3. Target Temperature Logic
            if pad_temp >= self.target_temp:
                if self.target_reached_time == 0:
                    self.target_reached_time = time.time()
                
                elapsed_mins = (time.time() - self.target_reached_time) / 60.0
                self.status_menu.title = f"Status: Target Reached ({elapsed_mins:.1f}/{self.duration_mins}m)"
                
                if elapsed_mins >= self.duration_mins:
                    self.stop_heating()
                    rumps.notification("Finished", "Target temperature and duration met.", "Cooling down.")
            else:
                self.target_reached_time = 0
                self.status_menu.title = f"Status: Heating... (Pad: {pad_temp}°C / CPU: {cpu_temp}°C)"
        else:
            self.status_menu.title = f"Status: Idle (Pad: {pad_temp}°C / CPU: {cpu_temp}°C)"

if __name__ == "__main__":
    MacHeaterApp().run()