
import os
import urllib.parse
import urllib.request
import urllib.error


THINGSPEAK_URL = "https://api.thingspeak.com/update.json"


def publish_telemetry(
    device_status=0,
    wifi_signal_strength=0,
    face_detection=0,
    recognized_student_count=0,
    attendance_status=0,
    session_status=0,
):
    api_key = os.getenv("THINGSPEAK_WRITE_API_KEY")

    if not api_key:
        raise RuntimeError("ThingSpeak API key is not configured.")

    data = {
        "api_key": api_key,
        "field1": device_status,
        "field2": wifi_signal_strength,
        "field3": face_detection,
        "field4": recognized_student_count,
        "field5": attendance_status,
        "field6": session_status,
    }

    encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        THINGSPEAK_URL,
        data=encoded_data,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = response.read().decode("utf-8").strip()

        if not result or result == "0":
            raise RuntimeError("ThingSpeak rejected the update.")

        return {
            "success": True,
            "entry_id": result,
        }

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"ThingSpeak connection failed: {error}"
        ) from error
