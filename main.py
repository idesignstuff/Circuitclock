# CircuitPython NeoPixel Circular Analog Clock with Web Interface
# For Raspberry Pi Pico W
# Uses a 60-LED WS2812 strip arranged in a circle
# Features:
# - WiFi client mode with fallback to AP mode
# - Web interface for configuration
# - Multiple animation modes

import time
import board
import neopixel
import rtc
import wifi
import socketpool
import ssl
import adafruit_requests
import microcontroller
import json
import re
import traceback
import os
import storage
import math
from adafruit_httpserver import HTTPServer, HTTPResponse, HTTPStatus

# Default configuration
DEFAULT_CONFIG = {
    "wifi_ssid": "",
    "wifi_password": "",
    "ap_ssid": "LEDClock",
    "ap_password": "clockconfig",
    "ap_mode": False,
    "brightness": 0.2,
    "hour_color": [255, 0, 0],
    "minute_color": [0, 255, 0],
    "second_color": [0, 0, 255],
    "marker_color": [32, 32, 32],
    "background_color": [0, 0, 0],
    "mode": "standard",
    "trail_length": 3,
    "pulse_speed": 5,
    "rainbow_speed": 5,
}

# LED strip configuration
NUM_PIXELS = 60
PIXEL_PIN = board.GP28  # Update to your Pico pin connected to LED data line

# Time settings
TIME_API_URL = "http://worldtimeapi.org/api/ip"
TIME_SYNC_INTERVAL = 3600  # Sync time every hour (in seconds)
CONFIG_FILE = "/config.json"

# Global variables
config = {}
pixels = None
last_sync_time = 0
last_second = -1
animation_counter = 0
server = None
pool = None
in_ap_mode = False

# Status LED pins for visual feedback
led = None
if hasattr(board, "LED"):
    led = digitalio.DigitalInOut(board.LED)
    led.direction = digitalio.Direction.OUTPUT

# HTML templates
SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LED Clock Setup</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f0f0f0;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .setup-section {
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h2 {
            margin-top: 0;
            color: #444;
        }
        label {
            display: block;
            margin: 10px 0 5px;
            font-weight: bold;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 8px;
            margin-bottom: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 15px;
            text-align: center;
            font-size: 16px;
            margin: 15px 0 5px;
            cursor: pointer;
            border-radius: 4px;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        .status {
            text-align: center;
            margin-top: 10px;
            font-weight: bold;
            color: #4CAF50;
            display: none;
        }
        .ap-info {
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            border-left: 4px solid #4CAF50;
            margin-bottom: 20px;
        }
        .ap-info p {
            margin: 5px 0;
        }
    </style>
</head>
<body>
    <h1>LED Clock WiFi Setup</h1>
    
    <div class="ap-info">
        <p><strong>You are connected to the clock's temporary WiFi network.</strong></p>
        <p>Please configure your home WiFi settings below to connect the clock to the internet.</p>
        <p>Once saved, the clock will attempt to connect to your WiFi network.</p>
    </div>
    
    <div class="setup-section">
        <h2>WiFi Settings</h2>
        <form id="wifi-form">
            <label for="wifi-ssid">WiFi Network Name (SSID):</label>
            <input type="text" id="wifi-ssid" name="wifi_ssid" required>
            
            <label for="wifi-password">WiFi Password:</label>
            <input type="password" id="wifi-password" name="wifi_password">
            
            <label for="ap-ssid">Fallback Access Point Name:</label>
            <input type="text" id="ap-ssid" name="ap_ssid" value="LEDClock" required>
            
            <label for="ap-password">Fallback Access Point Password:</label>
            <input type="password" id="ap-password" name="ap_password" value="clockconfig" required>
            <small>Password must be at least 8 characters</small>
            
            <button type="submit">Save Settings</button>
        </form>
        <div id="status" class="status">Settings saved! The clock will now restart and connect to your WiFi.</div>
    </div>
    
    <script>
        // Load current settings if available
        window.addEventListener('load', async () => {
            try {
                const response = await fetch('/api/wifi-config');
                if (response.ok) {
                    const config = await response.json();
                    
                    document.getElementById('wifi-ssid').value = config.wifi_ssid || '';
                    // Don't populate password for security
                    document.getElementById('ap-ssid').value = config.ap_ssid || 'LEDClock';
                    document.getElementById('ap-password').value = config.ap_password || 'clockconfig';
                }
            } catch (error) {
                console.error('Error loading settings:', error);
            }
        });
        
        // Form submission
        document.getElementById('wifi-form').addEventListener('submit', async (event) => {
            event.preventDefault();
            
            const apPassword = document.getElementById('ap-password').value;
            if (apPassword.length < 8) {
                alert('Access Point password must be at least 8 characters');
                return;
            }
            
            const formData = new FormData(event.target);
            const wifiConfig = {
                wifi_ssid: formData.get('wifi_ssid'),
                wifi_password: formData.get('wifi_password'),
                ap_ssid: formData.get('ap_ssid'),
                ap_password: formData.get('ap_password')
            };
            
            try {
                const response = await fetch('/api/wifi-config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(wifiConfig)
                });
                
                if (response.ok) {
                    document.getElementById('status').style.display = 'block';
                    // Give the user time to see the success message before restart
                    setTimeout(() => {
                        window.location.href = '/restart';
                    }, 5000);
                } else {
                    alert('Failed to save settings');
                }
            } catch (error) {
                console.error('Error saving settings:', error);
                alert('Error saving settings');
            }
        });
    </script>
</body>
</html>
"""

CONTROL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LED Clock Control</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f0f0f0;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .control-section {
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h2 {
            margin-top: 0;
            color: #444;
        }
        label {
            display: block;
            margin: 10px 0 5px;
            font-weight: bold;
        }
        input[type="range"] {
            width: 100%;
        }
        input[type="color"] {
            width: 60px;
            height: 30px;
        }
        .color-picker {
            display: flex;
            align-items: center;
        }
        .color-picker input {
            margin-right: 10px;
        }
        select {
            padding: 5px;
            width: 100%;
        }
        button {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 15px;
            text-align: center;
            font-size: 16px;
            margin: 15px 0 5px;
            cursor: pointer;
            border-radius: 4px;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        .status {
            text-align: center;
            margin-top: 10px;
            font-weight: bold;
            color: #4CAF50;
            display: none;
        }
        .network-info {
            background-color: #e9f7ef;
            padding: 10px 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .network-status {
            font-weight: bold;
        }
        .network-mode {
            background-color: #4CAF50;
            color: white;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
        }
        .ap-mode {
            background-color: #FFA500;
        }
        .nav-buttons {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav-button {
            background-color: #ddd;
            border: none;
            padding: 10px;
            border-radius: 4px;
            cursor: pointer;
            color: #333;
            font-weight: bold;
            text-decoration: none;
            text-align: center;
            flex: 1;
            margin: 0 5px;
        }
        .wifi-button {
            background-color: #2196F3;
            color: white;
        }
        .restart-button {
            background-color: #f44336;
            color: white;
        }
    </style>
</head>
<body>
    <h1>LED Clock Control Panel</h1>
    
    <div class="network-info">
        <span class="network-status">Network: <span id="network-name">Loading...</span></span>
        <span class="network-mode" id="network-mode">...</span>
    </div>
    
    <div class="nav-buttons">
        <a href="/wifi-setup" class="nav-button wifi-button">WiFi Settings</a>
        <a href="/restart" class="nav-button restart-button">Restart Clock</a>
    </div>
    
    <div class="control-section">
        <h2>Display Mode</h2>
        <select id="mode">
            <option value="standard">Standard</option>
            <option value="trail">Trail Effect</option>
            <option value="pulse">Pulse Effect</option>
            <option value="rainbow">Rainbow Effect</option>
        </select>
    </div>
    
    <div class="control-section">
        <h2>Colors</h2>
        <div class="color-picker">
            <input type="color" id="hour-color" value="#ff0000">
            <label for="hour-color">Hour Hand</label>
        </div>
        <div class="color-picker">
            <input type="color" id="minute-color" value="#00ff00">
            <label for="minute-color">Minute Hand</label>
        </div>
        <div class="color-picker">
            <input type="color" id="second-color" value="#0000ff">
            <label for="second-color">Second Hand</label>
        </div>
        <div class="color-picker">
            <input type="color" id="marker-color" value="#202020">
            <label for="marker-color">Hour Markers</label>
        </div>
        <div class="color-picker">
            <input type="color" id="background-color" value="#000000">
            <label for="background-color">Background</label>
        </div>
    </div>
    
    <div class="control-section">
        <h2>General Settings</h2>
        <label for="brightness">Brightness: <span id="brightness-value">20%</span></label>
        <input type="range" id="brightness" min="0" max="100" value="20">
        
        <div id="trail-settings" style="display: none;">
            <label for="trail-length">Trail Length: <span id="trail-length-value">3</span></label>
            <input type="range" id="trail-length" min="1" max="10" value="3">
        </div>
        
        <div id="pulse-settings" style="display: none;">
            <label for="pulse-speed">Pulse Speed: <span id="pulse-speed-value">5</span></label>
            <input type="range" id="pulse-speed" min="1" max="10" value="5">
        </div>
        
        <div id="rainbow-settings" style="display: none;">
            <label for="rainbow-speed">Rainbow Speed: <span id="rainbow-speed-value">5</span></label>
            <input type="range" id="rainbow-speed" min="1" max="10" value="5">
        </div>
    </div>
    
    <button id="save-settings">Save Settings</button>
    <div id="status" class="status">Settings saved successfully!</div>
    
    <script>
        // Update display of range values
        function updateRangeValue(elementId, valueId) {
            const element = document.getElementById(elementId);
            const valueElement = document.getElementById(valueId);
            valueElement.textContent = element.value + (elementId === 'brightness' ? '%' : '');
        }
        
        // Initialize mode-specific settings visibility
        function updateModeSettings() {
            const mode = document.getElementById('mode').value;
            document.getElementById('trail-settings').style.display = mode === 'trail' ? 'block' : 'none';
            document.getElementById('pulse-settings').style.display = mode === 'pulse' ? 'block' : 'none';
            document.getElementById('rainbow-settings').style.display = mode === 'rainbow' ? 'block' : 'none';
        }

        // Convert hex color to RGB array
        function hexToRgb(hex) {
            const r = parseInt(hex.substr(1, 2), 16);
            const g = parseInt(hex.substr(3, 2), 16);
            const b = parseInt(hex.substr(5, 2), 16);
            return [r, g, b];
        }
        
        // Convert RGB array to hex
        function rgbToHex(rgb) {
            return "#" + ((1 << 24) + (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]).toString(16).slice(1);
        }
        
        // Set up event listeners
        document.getElementById('brightness').addEventListener('input', () => updateRangeValue('brightness', 'brightness-value'));
        document.getElementById('trail-length').addEventListener('input', () => updateRangeValue('trail-length', 'trail-length-value'));
        document.getElementById('pulse-speed').addEventListener('input', () => updateRangeValue('pulse-speed', 'pulse-speed-value'));
        document.getElementById('rainbow-speed').addEventListener('input', () => updateRangeValue('rainbow-speed', 'rainbow-speed-value'));
        document.getElementById('mode').addEventListener('change', updateModeSettings);
        
        // Fetch network status
        async function getNetworkStatus() {
            try {
                const response = await fetch('/api/network-status');
                if (response.ok) {
                    const status = await response.json();
                    
                    document.getElementById('network-name').textContent = status.network_name || 'Not Connected';
                    
                    const modeElement = document.getElementById('network-mode');
                    modeElement.textContent = status.ap_mode ? 'AP MODE' : 'CLIENT MODE';
                    if (status.ap_mode) {
                        modeElement.classList.add('ap-mode');
                    } else {
                        modeElement.classList.remove('ap-mode');
                    }
                }
            } catch (error) {
                console.error('Error fetching network status:', error);
            }
        }
        
        // Load current settings when page loads
        window.addEventListener('load', async () => {
            // Get network status
            getNetworkStatus();
            
            try {
                const response = await fetch('/api/config');
                if (response.ok) {
                    const config = await response.json();
                    
                    // Set mode
                    document.getElementById('mode').value = config.mode;
                    
                    // Set colors
                    document.getElementById('hour-color').value = rgbToHex(config.hour_color);
                    document.getElementById('minute-color').value = rgbToHex(config.minute_color);
                    document.getElementById('second-color').value = rgbToHex(config.second_color);
                    document.getElementById('marker-color').value = rgbToHex(config.marker_color);
                    document.getElementById('background-color').value = rgbToHex(config.background_color);
                    
                    // Set range inputs
                    document.getElementById('brightness').value = Math.round(config.brightness * 100);
                    document.getElementById('trail-length').value = config.trail_length;
                    document.getElementById('pulse-speed').value = config.pulse_speed;
                    document.getElementById('rainbow-speed').value = config.rainbow_speed;
                    
                    // Update display values
                    updateRangeValue('brightness', 'brightness-value');
                    updateRangeValue('trail-length', 'trail-length-value');
                    updateRangeValue('pulse-speed', 'pulse-speed-value');
                    updateRangeValue('rainbow-speed', 'rainbow-speed-value');
                    
                    // Update mode-specific settings visibility
                    updateModeSettings();
                }
            } catch (error) {
                console.error('Error loading settings:', error);
            }
        });
        
        // Save button handler
        document.getElementById('save-settings').addEventListener('click', async () => {
            const newConfig = {
                mode: document.getElementById('mode').value,
                hour_color: hexToRgb(document.getElementById('hour-color').value),
                minute_color: hexToRgb(document.getElementById('minute-color').value),
                second_color: hexToRgb(document.getElementById('second-color').value),
                marker_color: hexToRgb(document.getElementById('marker-color').value),
                background_color: hexToRgb(document.getElementById('background-color').value),
                brightness: parseInt(document.getElementById('brightness').value) / 100,
                trail_length: parseInt(document.getElementById('trail-length').value),
                pulse_speed: parseInt(document.getElementById('pulse-speed').value),
                rainbow_speed: parseInt(document.getElementById('rainbow-speed').value)
            };
            
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(newConfig)
                });
                
                if (response.ok) {
                    const statusElement = document.getElementById('status');
                    statusElement.style.display = 'block';
                    setTimeout(() => {
                        statusElement.style.display = 'none';
                    }, 3000);
                } else {
                    alert('Failed to save settings');
                }
            } catch (error) {
                console.error('Error saving settings:', error);
                alert('Error saving settings');
            }
        });
    </script>
</body>
</html>
"""

WIFI_SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LED Clock WiFi Setup</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f0f0f0;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .setup-section {
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h2 {
            margin-top: 0;
            color: #444;
        }
        label {
            display: block;
            margin: 10px 0 5px;
            font-weight: bold;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 8px;
            margin-bottom: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 15px;
            text-align: center;
            font-size: 16px;
            margin: 15px 0 5px;
            cursor: pointer;
            border-radius: 4px;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        .status {
            text-align: center;
            margin-top: 10px;
            font-weight: bold;
            color: #4CAF50;
            display: none;
        }
        .nav-buttons {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav-button {
            background-color: #ddd;
            border: none;
            padding: 10px;
            border-radius: 4px;
            cursor: pointer;
            color: #333;
            font-weight: bold;
            text-decoration: none;
            text-align: center;
            flex: 1;
            margin: 0 5px;
        }
        .back-button {
            background-color: #607D8B;
            color: white;
        }
    </style>
</head>
<body>
    <h1>LED Clock WiFi Setup</h1>
    
    <div class="nav-buttons">
        <a href="/" class="nav-button back-button">Back to Controls</a>
    </div>
    
    <div class="setup-section">
        <h2>WiFi Settings</h2>
        <form id="wifi-form">
            <label for="wifi-ssid">WiFi Network Name (SSID):</label>
            <input type="text" id="wifi-ssid" name="wifi_ssid" required>
            
            <label for="wifi-password">WiFi Password:</label>
            <input type="password" id="wifi-password" name="wifi_password">
            
            <label for="ap-ssid">Fallback Access Point Name:</label>
            <input type="text" id="ap-ssid" name="ap_ssid" required>
            
            <label for="ap-password">Fallback Access Point Password:</label>
            <input type="password" id="ap-password" name="ap_password" required>
            <small>Password must be at least 8 characters</small>
            
            <button type="submit">Save WiFi Settings</button>
        </form>
        <div id="status" class="status">Settings saved! The clock will now restart and connect to your WiFi.</div>
    </div>
    
    <script>
        // Load current settings if available
        window.addEventListener('load', async () => {
            try {
                const response = await fetch('/api/wifi-config');
                if (response.ok) {
                    const config = await response.json();
                    
                    document.getElementById('wifi-ssid').value = config.wifi_ssid || '';
                    // Don't populate password for security
                    document.getElementById('ap-ssid').value = config.ap_ssid || 'LEDClock';
                    document.getElementById('ap-password').value = config.ap_password || 'clockconfig';
                }
            } catch (error) {
                console.error('Error loading settings:', error);
            }
        });
        
        // Form submission
        document.getElementById('wifi-form').addEventListener('submit', async (event) => {
            event.preventDefault();
            
            const apPassword = document.getElementById('ap-password').value;
            if (apPassword.length < 8) {
                alert('Access Point password must be at least 8 characters');
                return;
            }
            
            const formData = new FormData(event.target);
            const wifiConfig = {
                wifi_ssid: formData.get('wifi_ssid'),
                wifi_password: formData.get('wifi_password'),
                ap_ssid: formData.get('ap_ssid'),
                ap_password: formData.get('ap_password')
            };
            
            try {
                const response = await fetch('/api/wifi-config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(wifiConfig)
                });
                
                if (response.ok) {
                    document.getElementById('status').style.display = 'block';
                    // Give the user time to see the success message before restart
                    setTimeout(() => {
                        window.location.href = '/restart';
                    }, 5000);
                } else {
                    alert('Failed to save settings');
                }
            } catch (error) {
                console.error('Error saving settings:', error);
                alert('Error saving settings');
            }
        });
    </script>
</body>
</html>
"""

def load_config():
    """Load configuration from file or use defaults"""
    global config
    
    try:
        # Check if config file exists
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded_config = json.loads(f.read())
                # Update config with loaded values, keeping defaults for any missing keys
                for key, value in DEFAULT_CONFIG.items():
                    config[key] = loaded_config.get(key, value)
                print("Configuration loaded from file")
        except (OSError, ValueError) as e:
            print(f"Could not load config ({e}), using defaults")
            config = DEFAULT_CONFIG.copy()
            save_config()  # Create the default config file
    except Exception as e:
        print(f"Error in load_config: {e}")
        traceback.print_exception(e, e, e.__traceback__)
        config = DEFAULT_CONFIG.copy()

def save_config():
    """Save configuration to file"""
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(json.dumps(config))
        print("Configuration saved to file")
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        traceback.print_exception(e, e, e.__traceback__)
        return False

def blink_led(times=3, delay=0.2):
    """Blink the onboard LED to indicate status"""
    if led:
        for _ in range(times):
            led.value = True
            time.sleep(delay)
            led.value = False
            time.sleep(delay)

def start_ap_mode():
    """Start access point mode for configuration"""
    global in_ap_mode, pool, server
    
    print("Starting access point mode")
    in_ap_mode = True
    
    try:
        # Create access point with the name and password from config
        ap_ssid = config.get("ap_ssid", "LEDClock")
        ap_password = config.get("ap_password", "clockconfig")
        
        print(f"Creating access point '{ap_ssid}'")
        wifi.radio.start_ap(ssid=ap_ssid, password=ap_password)
        
        print(f"Access point created with IP {wifi.radio.ipv4_address_ap}")
        
        # Blink LED to indicate AP mode
        blink_led(5, 0.1)
        
        # Start the web server for configuration
        pool = socketpool.SocketPool(wifi.radio)
        server = HTTPServer(pool)
        setup_routes(True)
        server.start(str(wifi.radio.ipv4_address_ap), 80)
        print(f"AP mode web server started at http://{wifi.radio.ipv4_address_ap}")
        
        return True
    except Exception as e:
        print(f"Failed to start AP mode: {e}")
        traceback.print_exception(e, e, e.__traceback__)
        return False

def try_connect_wifi():
    """Try to connect to the configured WiFi network"""
    global in_ap_mode, pool, server
    
    # Don't try to connect if no SSID is configured
    if not config.get("wifi_ssid"):
        print("No WiFi SSID configured, starting AP mode")
        return False
