import pywifi
import sqlite3
import os
from time import sleep


# =========================================================
# PROJECT PATH
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

DATABASE_PATH = os.path.join(
    BASE_DIR,
    "Database",
    "attendance.db"
)


STUDENT_ID = "20241ise0219"


# =========================================================
# GET REGISTERED WIFI
# =========================================================

connection = sqlite3.connect(DATABASE_PATH)
cursor = connection.cursor()

cursor.execute(
    """
    SELECT name, wifi_ssid
    FROM students
    WHERE student_id = ?
    """,
    (STUDENT_ID,)
)

student = cursor.fetchone()

connection.close()


if student is None:
    print("Student is not registered.")
    exit()


student_name = student[0]
registered_wifi = student[1]


if not registered_wifi:
    print("No Wi-Fi is registered for this student.")
    exit()


# =========================================================
# SCAN WIFI
# =========================================================

wifi = pywifi.PyWiFi()
interfaces = wifi.interfaces()

if not interfaces:
    print("ERROR: No Wi-Fi adapter found.")
    exit()


interface = interfaces[0]


print("Student:", student_name)
print("Student ID:", STUDENT_ID)
print("Registered Wi-Fi:", registered_wifi)
print()
print("Scanning nearby Wi-Fi...")
print()


interface.scan()

sleep(3)

results = interface.scan_results()


# =========================================================
# COMPARE WIFI
# =========================================================

registered_wifi_normalized = registered_wifi.strip().lower()

wifi_found = False


for network in results:

    detected_ssid = network.ssid.strip()

    if not detected_ssid:
        continue

    if detected_ssid.lower() == registered_wifi_normalized:

        wifi_found = True

        print("--------------------------------------")
        print("REGISTERED WI-FI DETECTED")
        print("SSID:", detected_ssid)
        print("Signal:", network.signal)
        print("Student:", student_name)
        print("Student ID:", STUDENT_ID)
        print("STATUS: DEVICE PRESENT")
        print("--------------------------------------")

        break


# =========================================================
# FINAL RESULT
# =========================================================

if not wifi_found:

    print("--------------------------------------")
    print("REGISTERED WI-FI NOT DETECTED")
    print("Student:", student_name)
    print("Student ID:", STUDENT_ID)
    print("STATUS: DEVICE NOT PRESENT")
    print("--------------------------------------")