
try:
    import pywifi
except ImportError:
    pywifi = None

from time import sleep


def scan_wifi_ssids():

    if pywifi is None:
        raise RuntimeError(
            "Wi-Fi scanning is unavailable on this server."
        )

    wifi = pywifi.PyWiFi()
    interfaces = wifi.interfaces()

    if not interfaces:
        raise RuntimeError("No Wi-Fi adapter found.")

    interface = interfaces[0]
    interface.scan()
    sleep(3)

    return {
        network.ssid.strip()
        for network in interface.scan_results()
        if network.ssid and network.ssid.strip()
    }


# =========================================================
# CHECK REGISTERED WI-FI PRESENCE
# =========================================================

def check_wifi_presence(student):

    registered_wifi = student[2]

    if not registered_wifi:
        print("No Wi-Fi is registered for this student.")
        return False

    print("Scanning nearby Wi-Fi...")
    print()

    registered_wifi_normalized = (
        registered_wifi.strip().lower()
    )

    available_ssids = scan_wifi_ssids()

    detected_ssid = next(
        (
            ssid for ssid in available_ssids
            if ssid.lower() == registered_wifi_normalized
        ),
        None
    )

    if detected_ssid:
        print("--------------------------------------")
        print("REGISTERED WI-FI DETECTED")
        print("SSID:", detected_ssid)
        print("Student:", student[1])
        print("Student ID:", student[0])
        print("STATUS: REGISTERED SSID DETECTED")
        print("--------------------------------------")
        return True

    print("--------------------------------------")
    print("REGISTERED WI-FI NOT DETECTED")
    print("Student:", student[1])
    print("Student ID:", student[0])
    print("STATUS: REGISTERED SSID NOT DETECTED")
    print("--------------------------------------")

    return False
