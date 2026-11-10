import pywifi
from time import sleep

wifi = pywifi.PyWiFi()

interfaces = wifi.interfaces()

if not interfaces:
    print("ERROR: No Wi-Fi adapter found.")
    exit()

interface = interfaces[0]

print("Wi-Fi adapter found.")
print("Starting Wi-Fi scan...")
print()

interface.scan()

sleep(3)

results = interface.scan_results()

print("Nearby Wi-Fi networks:")
print("--------------------------------------")

seen = set()

for network in results:

    ssid = network.ssid.strip()

    if not ssid:
        continue

    if ssid in seen:
        continue

    seen.add(ssid)

    print("SSID:", ssid)
    print("Signal:", network.signal)
    print("--------------------------------------")

print()
print("Total networks found:", len(seen))