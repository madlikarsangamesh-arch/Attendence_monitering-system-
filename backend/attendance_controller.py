import sqlite3
from datetime import datetime

from services.face_service import recognize_face_camera
from services.wifi_service import check_wifi_presence


# =========================================================
# CONFIGURATION
# =========================================================

STUDENT_ID = "20241ise0219"

DATABASE_PATH = "Database/attendance.db"


# =========================================================
# GET STUDENT FROM DATABASE
# =========================================================

def get_student(student_id):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT student_id, name, wifi_ssid
        FROM students
        WHERE student_id = ?
        """,
        (student_id,)
    )

    student = cursor.fetchone()

    connection.close()

    return student


# =========================================================
# MARK ATTENDANCE
# =========================================================

def mark_attendance(student_id):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()

    now = datetime.now()

    today = now.strftime(
        "%Y-%m-%d"
    )

    current_time = now.strftime(
        "%H:%M:%S"
    )


    # -----------------------------------------------------
    # CHECK DUPLICATE ATTENDANCE
    # -----------------------------------------------------

    cursor.execute(
        """
        SELECT id
        FROM attendance
        WHERE student_id = ?
        AND date = ?
        """,
        (
            student_id,
            today
        )
    )

    existing_record = cursor.fetchone()


    if existing_record:

        connection.close()

        print()
        print(
            "ATTENDANCE ALREADY MARKED TODAY"
        )

        print(
            "Student ID:",
            student_id
        )

        print(
            "Date:",
            today
        )

        return False


    # -----------------------------------------------------
    # INSERT ATTENDANCE
    # -----------------------------------------------------

    cursor.execute(
        """
        INSERT INTO attendance
        (student_id, date, time, status)
        VALUES (?, ?, ?, ?)
        """,
        (
            student_id,
            today,
            current_time,
            "PRESENT"
        )
    )


    connection.commit()

    connection.close()


    print()
    print(
        "ATTENDANCE MARKED SUCCESSFULLY"
    )

    print(
        "Student ID:",
        student_id
    )

    print(
        "Date:",
        today
    )

    print(
        "Time:",
        current_time
    )

    print(
        "Status: PRESENT"
    )

    return True


# =========================================================
# MAIN ATTENDANCE VERIFICATION
# =========================================================

def main():

    print()
    print(
        "======================================"
    )

    print(
        "AttendAIoT Attendance Verification"
    )

    print(
        "======================================"
    )

    print()


    # =====================================================
    # STEP 1: FIND STUDENT
    # =====================================================

    student = get_student(
        STUDENT_ID
    )


    if student is None:

        print(
            "Student is not registered."
        )

        return


    print(
        "Student found"
    )

    print(
        "Student ID:",
        student[0]
    )

    print(
        "Name:",
        student[1]
    )

    print(
        "Registered Wi-Fi:",
        student[2]
    )

    print()


    # =====================================================
    # STEP 2: CHECK WI-FI
    # =====================================================

    wifi_present = check_wifi_presence(
        student
    )


    # =====================================================
    # STEP 3: FACE RECOGNITION
    # =====================================================

    recognized_student = recognize_face_camera()


    # =====================================================
    # STEP 4: SHOW VERIFICATION RESULT
    # =====================================================

    print()

    print(
        "======================================"
    )

    print(
        "CURRENT VERIFICATION RESULT"
    )

    print(
        "======================================"
    )

    print(
        "Wi-Fi present:",
        wifi_present
    )

    print(
        "Recognized student:",
        recognized_student
    )


    # =====================================================
    # STEP 5: FINAL DECISION
    # =====================================================

    if (
        wifi_present
        and recognized_student == STUDENT_ID
    ):

        print()

        print(
            "ATTENDANCE VERIFICATION PASSED"
        )

        print(
            "Student:",
            student[1]
        )

        print(
            "Student ID:",
            STUDENT_ID
        )


        mark_attendance(
            STUDENT_ID
        )


    elif not wifi_present:

        print()

        print(
            "ATTENDANCE VERIFICATION FAILED"
        )

        print(
            "Reason: Registered Wi-Fi not detected."
        )


    else:

        print()

        print(
            "ATTENDANCE VERIFICATION FAILED"
        )

        print(
            "Reason: Face not recognized."
        )


    print(
        "======================================"
    )


# =========================================================
# PROGRAM ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()