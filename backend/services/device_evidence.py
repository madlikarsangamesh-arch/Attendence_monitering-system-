try:
    import pywifi
except ImportError:
    pywifi = None


def get_device_evidence_status():

    interfaces = []

    if pywifi is not None:
        try:
            interfaces = pywifi.PyWiFi().interfaces()
        except Exception:
            interfaces = []

    return {
        "provider_configured": False,
        "provider": None,
        "individual_device_presence_available": False,
        "local_wifi_adapters": [
            {
                "name": interface.name(),
                "can_scan_access_points": callable(
                    getattr(interface, "scan", None)
                ),
                "can_report_connected_clients": False,
            }
            for interface in interfaces
        ],
        "message": (
            "The local adapter can scan access points but cannot identify "
            "individual student devices. Configure an authenticated router, "
            "controller, RADIUS, or enrolled-device provider."
        ),
    }