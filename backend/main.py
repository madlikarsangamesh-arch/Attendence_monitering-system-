import asyncio

import base64

import binascii

import sqlite3

import cv2

from time import monotonic

from contextlib import asynccontextmanager, closing

from datetime import datetime

from pathlib import Path



from fastapi import FastAPI, HTTPException

from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field



from backend.services.device_evidence import get_device_evidence_status

from backend.services.face_service import (

    invalidate_face_model,

    is_face_enrolled,

    recognize_face_image,

    validate_face_enrollment_image,

)

from backend.services.session_service import (

    get_session_status,

    get_settings,

    initialize_session_schema,

    record_face_observation,

    save_settings,

    session_deadline_watcher,

    start_session,

    stop_session,

)

from backend.services.wifi_service import scan_wifi_ssids

from backend.services.thingspeak_service import publish_telemetry





# =========================================================

# LIFESPAN

# =========================================================



@asynccontextmanager

async def lifespan(app: FastAPI):



    from backend.database import initialize_database



    initialize_database()

    initialize_session_schema()



    watcher = asyncio.create_task(session_deadline_watcher())



    try:

        yield

    finally:

        watcher.cancel()



        try:

            await watcher

        except asyncio.CancelledError:

            pass





# =========================================================

# FASTAPI APPLICATION

# =========================================================



app = FastAPI(

    title="AttendAIoT API",

    lifespan=lifespan,

)



DATABASE_PATH = (

    Path(__file__).resolve().parents[1]

    / "Database"

    / "attendance.db"

)





# =========================================================

# HELPERS

# =========================================================



def hotspot_visible(registered_ssid: str | None) -> bool:

    """

    True if the student's registered hotspot name is currently

    visible in a Wi-Fi scan. May raise RuntimeError if the scan fails.

    """



    registered = (registered_ssid or "").strip()



    if not registered:

        return False



    available = scan_wifi_ssids()



    return any(

        ssid.casefold() == registered.casefold()

        for ssid in available

    )





def mark_attendance_row(

    connection,

    student_id: str,

    status: str,

) -> bool:

    """

    Insert one attendance row per student per day.

    Returns False if a row already exists for today.

    """



    now = datetime.now()

    today = now.strftime("%Y-%m-%d")



    exists = connection.execute(

        """

        SELECT 1

        FROM attendance

        WHERE student_id = ? COLLATE NOCASE

          AND date = ?

        """,

        (student_id, today),

    ).fetchone()



    if exists:

        return False



    connection.execute(

        """

        INSERT INTO attendance (student_id, date, time, status)

        VALUES (?, ?, ?, ?)

        """,

        (

            student_id,

            today,

            now.strftime("%H:%M:%S"),

            status,

        ),

    )



    return True





# =========================================================

# REQUEST MODELS

# =========================================================



class ScanRequest(BaseModel):

    image_base64: str





class SessionSettings(BaseModel):

    arrival_scan_minutes: int = Field(ge=1, le=30)

    session_deadline_minutes: int = Field(ge=45, le=360)

    disconnect_grace_minutes: int = Field(ge=0, le=30)

    minimum_verified_minutes: int = Field(ge=1, le=360)





class SessionFrameRequest(BaseModel):

    image_base64: str





class StudentRegistrationRequest(BaseModel):

    student_id: str = Field(min_length=1, max_length=100)

    name: str = Field(min_length=1, max_length=200)

    wifi_ssid: str = Field(min_length=1, max_length=200)

    image_base64: str | None = None





class StudentUpdateRequest(BaseModel):

    name: str = Field(min_length=1, max_length=200)

    wifi_ssid: str = Field(max_length=200)

    image_base64: str | None = None





# =========================================================

# CORS

# =========================================================



app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)





# =========================================================

# HOME

# =========================================================



@app.get("/")

def home():



    return {

        "message": "AttendAIoT Backend is running"

    }





# =========================================================

# HEALTH CHECK

# =========================================================



@app.get("/health")

def health():



    return {

        "status": "online"

    }





@app.get("/camera/test-stream")
def test_camera_stream():
    stream_url = "http://192.168.137.23:81/stream"
    cap = cv2.VideoCapture(stream_url)

    try:
        if not cap.isOpened():
            raise HTTPException(
                status_code=502,
                detail="Could not connect to ESP32-CAM stream",
            )

        success, frame = cap.read()
        if not success or frame is None:
            raise HTTPException(
                status_code=502,
                detail="Connected, but no frame received",
            )

        height, width = frame.shape[:2]
        return {
            "connected": True,
            "frame_received": True,
            "width": width,
            "height": height,
            "message": "ESP32-CAM live stream is working!",
        }
    finally:
        cap.release()


# =========================================================

# SESSION SETTINGS

# =========================================================



@app.get("/session-settings")

def read_session_settings():



    return get_settings()





@app.put("/session-settings")

def update_session_settings(settings: SessionSettings):



    if (

        settings.arrival_scan_minutes

        > settings.session_deadline_minutes

    ):

        raise HTTPException(

            status_code=422,

            detail="Arrival scanning cannot outlast the session deadline.",

        )



    if (

        settings.minimum_verified_minutes

        > settings.session_deadline_minutes

    ):

        raise HTTPException(

            status_code=422,

            detail="Minimum verified attendance cannot exceed the session deadline.",

        )



    try:

        saved = save_settings(settings.model_dump())



    except ValueError as error:

        raise HTTPException(

            status_code=409,

            detail=str(error),

        )



    return {

        "settings": saved

    }





# =========================================================

# DEVICE EVIDENCE STATUS

# =========================================================



@app.get("/device-evidence/status")

def device_evidence_status():



    return get_device_evidence_status()





# =========================================================

# ATTENDANCE SESSIONS

# =========================================================



@app.post("/sessions")

def create_attendance_session():



    try:

        return start_session()



    except RuntimeError as error:

        raise HTTPException(

            status_code=409,

            detail=str(error),

        )





@app.get("/sessions/current")

def current_attendance_session():



    return get_session_status()





@app.get("/sessions/{session_id}")

def attendance_session(session_id: str):



    result = get_session_status(session_id)



    if result["session"] is None:

        raise HTTPException(

            status_code=404,

            detail="Attendance session not found.",

        )



    return result





@app.post("/sessions/{session_id}/stop")

def stop_attendance_session(session_id: str):



    try:

        return stop_session(session_id)



    except LookupError as error:

        raise HTTPException(

            status_code=404,

            detail=str(error),

        )





# =========================================================

# PROCESS CAMERA FRAME DURING SESSION

# =========================================================



@app.post("/sessions/{session_id}/frame")

def process_session_frame(

    session_id: str,

    request: SessionFrameRequest,

):



    try:

        image_bytes = base64.b64decode(

            request.image_base64,

            validate=True,

        )



    except (binascii.Error, ValueError):

        raise HTTPException(

            status_code=400,

            detail="Invalid image data.",

        )



    if (

        not image_bytes

        or len(image_bytes) > 5 * 1024 * 1024

    ):

        raise HTTPException(

            status_code=400,

            detail="Image must be non-empty and no larger than 5MB.",

        )



    try:

        status = get_session_status(session_id)



        if status["session"] is None:

            raise HTTPException(

                status_code=404,

                detail="Attendance session not found.",

            )



        if status["session"]["status"] != "ARRIVAL_SCANNING":



            return {

                "recognized_student_ids": [],

                "arrival_scanning": False,

                "message": "Arrival scanning is not active.",

            }



        recognized_student_id = recognize_face_image(

            image_bytes

        )



        recognized_ids = (

            [recognized_student_id]

            if recognized_student_id

            else []

        )



        observed_ids = record_face_observation(

            session_id,

            recognized_ids,

        )



    except LookupError as error:

        raise HTTPException(

            status_code=404,

            detail=str(error),

        )



    return {

        "recognized_student_ids": observed_ids,

        "arrival_scanning": True,

        "attendance_marked": False,

        "message": (

            "Face detected; waiting for authenticated device verification."

            if observed_ids

            else "No enrolled face recognized; scanning will retry."

        ),

    }





# =========================================================

# AUTHENTICATED DEVICE EVENTS

# =========================================================



@app.post("/sessions/{session_id}/device-events")

def receive_authenticated_device_event(session_id: str):



    raise HTTPException(

        status_code=503,

        detail=(

            "No authenticated router, controller, RADIUS, or enrolled-device "

            "provider is configured. SSID scans cannot create device events."

        ),

    )





# =========================================================

# REGISTER / REACTIVATE STUDENT

# =========================================================



@app.post("/students")

def add_student(

    student_id: str | None = None,

    name: str | None = None,

    wifi_ssid: str | None = None,

    request: StudentRegistrationRequest | None = None,

):



    # Support both JSON request and query parameters

    student_id = (

        request.student_id

        if request

        else student_id or ""

    ).strip()



    name = (

        request.name

        if request

        else name or ""

    ).strip()



    wifi_ssid = (

        request.wifi_ssid

        if request

        else wifi_ssid or ""

    ).strip()



    if not student_id or not name or not wifi_ssid:

        raise HTTPException(

            status_code=422,

            detail="Student ID, name, and Wi-Fi SSID are required.",

        )



    # -----------------------------------------------------

    # Decode face image

    # -----------------------------------------------------



    image_bytes = None



    image_base64 = (

        request.image_base64

        if request

        else None

    )



    if image_base64:



        try:

            image_bytes = base64.b64decode(

                image_base64,

                validate=True,

            )



        except (binascii.Error, ValueError):

            raise HTTPException(

                status_code=400,

                detail="Invalid face image data.",

            )



        if (

            not image_bytes

            or len(image_bytes) > 5 * 1024 * 1024

        ):

            raise HTTPException(

                status_code=400,

                detail="Face image must be non-empty and no larger than 5MB.",

            )



        try:

            validate_face_enrollment_image(

                image_bytes

            )



        except ValueError as error:

            raise HTTPException(

                status_code=422,

                detail=str(error),

            )



    # Make sure schema/migrations have been initialized

    initialize_session_schema()



    reactivated = False



    with closing(

        sqlite3.connect(

            DATABASE_PATH,

            timeout=10,

        )

    ) as connection:



        connection.execute("BEGIN IMMEDIATE")



        # -------------------------------------------------

        # Check whether the ID already exists

        # -------------------------------------------------



        existing = connection.execute(

            """

            SELECT student_id, name, is_active

            FROM students

            WHERE student_id = ? COLLATE NOCASE

            """,

            (student_id,),

        ).fetchone()



        # -------------------------------------------------

        # EXISTING ACTIVE STUDENT

        # Do not create a duplicate

        # -------------------------------------------------



        if existing and existing[2] == 1:



            connection.rollback()



            return {

                "message": "Student is already registered.",

                "student_id": existing[0],

                "name": existing[1],

                "already_exists": True,

                "reactivated": False,

            }



        # -------------------------------------------------

        # EXISTING ARCHIVED STUDENT

        # Reactivate the same database record

        # -------------------------------------------------



        if existing and existing[2] == 0:



            student_id = existing[0]



            connection.execute(

                """

                UPDATE students

                SET name = ?,

                    wifi_ssid = ?,

                    is_active = 1

                WHERE student_id = ? COLLATE NOCASE

                """,

                (

                    name,

                    wifi_ssid,

                    student_id,

                ),

            )



            reactivated = True



        # -------------------------------------------------

        # NEW STUDENT

        # -------------------------------------------------



        else:



            connection.execute(

                """

                INSERT INTO students (

                    student_id,

                    name,

                    wifi_ssid,

                    is_active

                )

                VALUES (?, ?, ?, 1)

                """,

                (

                    student_id,

                    name,

                    wifi_ssid,

                ),

            )



        # -------------------------------------------------

        # Save new face sample, if provided

        # Existing face samples are NOT deleted

        # -------------------------------------------------



        if image_bytes:



            connection.execute(

                """

                INSERT INTO student_face_samples (

                    student_id,

                    image_bytes,

                    created_at

                )

                VALUES (?, ?, ?)

                """,

                (

                    student_id,

                    image_bytes,

                    datetime.now()

                    .astimezone()

                    .isoformat(timespec="milliseconds"),

                ),

            )



        connection.commit()



    # -----------------------------------------------------

    # Refresh face-recognition model if image was added

    # -----------------------------------------------------



    if image_bytes:

        invalidate_face_model()



    # -----------------------------------------------------

    # Response

    # -----------------------------------------------------



    if reactivated:



        message = (

            "Student reactivated successfully. "

            "Previous attendance history and face samples were preserved."

        )



    elif image_bytes:



        message = (

            "Student registered successfully with face enrollment."

        )



    else:



        message = (

            "Student registered successfully without face enrollment."

        )



    return {

        "message": message,

        "student_id": student_id,

        "name": name,

        "wifi_ssid": wifi_ssid,

        "already_exists": False,

        "reactivated": reactivated,

        "face_enrolled": bool(image_bytes),

    }





# =========================================================

# GET ACTIVE STUDENTS

# =========================================================



@app.get("/students")

def get_students():



    with closing(

        sqlite3.connect(DATABASE_PATH)

    ) as connection:



        cursor = connection.cursor()



        cursor.execute(

            """

            SELECT id, student_id, name, wifi_ssid

            FROM students

            WHERE is_active = 1

            ORDER BY name COLLATE NOCASE, student_id

            """

        )



        students = [

            (

                *student,

                is_face_enrolled(student[1]),

            )

            for student in cursor.fetchall()

        ]



    return {

        "students": students

    }





# =========================================================

# UPDATE STUDENT

# =========================================================



@app.put("/students/{student_id}")

def update_student(

    student_id: str,

    request: StudentUpdateRequest,

):



    name = request.name.strip()

    wifi_ssid = request.wifi_ssid.strip()



    if not name:

        raise HTTPException(

            status_code=422,

            detail="Student name is required.",

        )



    image_bytes = None



    if request.image_base64:



        try:

            image_bytes = base64.b64decode(

                request.image_base64,

                validate=True,

            )



        except (binascii.Error, ValueError):

            raise HTTPException(

                status_code=400,

                detail="Invalid face image data.",

            )



        if (

            not image_bytes

            or len(image_bytes) > 5 * 1024 * 1024

        ):

            raise HTTPException(

                status_code=400,

                detail="Face image must be non-empty and no larger than 5MB.",

            )



        try:

            validate_face_enrollment_image(

                image_bytes

            )



        except ValueError as error:

            raise HTTPException(

                status_code=422,

                detail=str(error),

            )



    initialize_session_schema()



    with closing(

        sqlite3.connect(

            DATABASE_PATH,

            timeout=10,

        )

    ) as connection:



        connection.execute("BEGIN IMMEDIATE")



        student = connection.execute(

            """

            SELECT student_id

            FROM students

            WHERE student_id = ? COLLATE NOCASE

              AND is_active = 1

            """,

            (student_id,),

        ).fetchone()



        if student is None:



            connection.rollback()



            raise HTTPException(

                status_code=404,

                detail="Student is not registered.",

            )



        in_active_session = connection.execute(

            """

            SELECT 1

            FROM session_students ss

            JOIN attendance_sessions sessions

              ON sessions.session_id = ss.session_id

            WHERE ss.student_id = ?

              AND sessions.status IN (

                  'ARRIVAL_SCANNING',

                  'MONITORING'

              )

            LIMIT 1

            """,

            (student[0],),

        ).fetchone()



        if in_active_session:



            connection.rollback()



            raise HTTPException(

                status_code=409,

                detail=(

                    "This student is part of the active attendance session. "

                    "Stop that session before editing their details."

                ),

            )



        connection.execute(

            """

            UPDATE students

            SET name = ?,

                wifi_ssid = ?

            WHERE student_id = ? COLLATE NOCASE

            """,

            (

                name,

                wifi_ssid,

                student[0],

            ),

        )



        if image_bytes:



            connection.execute(

                """

                INSERT INTO student_face_samples (

                    student_id,

                    image_bytes,

                    created_at

                )

                VALUES (?, ?, ?)

                """,

                (

                    student[0],

                    image_bytes,

                    datetime.now()

                    .astimezone()

                    .isoformat(timespec="milliseconds"),

                ),

            )



        connection.commit()



    if image_bytes:

        invalidate_face_model()



    return {

        "message": (

            "Student details and face profile updated."

            if image_bytes

            else "Student details updated."

        ),

        "student_id": student[0],

        "name": name,

        "wifi_ssid": wifi_ssid,

        "face_updated": bool(image_bytes),

    }





# =========================================================

# ARCHIVE STUDENT

# =========================================================



@app.delete("/students/{student_id}")

def archive_student(student_id: str):



    initialize_session_schema()



    with closing(

        sqlite3.connect(

            DATABASE_PATH,

            timeout=10,

        )

    ) as connection:



        connection.execute("BEGIN IMMEDIATE")



        student = connection.execute(

            """

            SELECT student_id

            FROM students

            WHERE student_id = ? COLLATE NOCASE

              AND is_active = 1

            """,

            (student_id,),

        ).fetchone()



        if student is None:



            connection.rollback()



            raise HTTPException(

                status_code=404,

                detail="Student is not registered.",

            )



        in_active_session = connection.execute(

            """

            SELECT 1

            FROM session_students ss

            JOIN attendance_sessions sessions

              ON sessions.session_id = ss.session_id

            WHERE ss.student_id = ?

              AND sessions.status IN (

                  'ARRIVAL_SCANNING',

                  'MONITORING'

              )

            LIMIT 1

            """,

            (student[0],),

        ).fetchone()



        if in_active_session:



            connection.rollback()



            raise HTTPException(

                status_code=409,

                detail=(

                    "This student is part of the active attendance session. "

                    "Stop that session before removing them."

                ),

            )



        connection.execute(

            """

            UPDATE students

            SET is_active = 0

            WHERE student_id = ? COLLATE NOCASE

            """,

            (student[0],),

        )



        connection.commit()



    return {

        "message": (

            "Student archived from the active roster. "

            "Attendance history was preserved."

        ),

        "student_id": student[0],

    }





# =========================================================

# WI-FI STATUS FOR ALL STUDENTS

# =========================================================



@app.get("/wifi/status")

def get_wifi_status():



    try:

        available_ssids = scan_wifi_ssids()



    except RuntimeError as error:

        raise HTTPException(

            status_code=503,

            detail=str(error),

        )



    available_folded = {

        ssid.casefold() for ssid in available_ssids

    }



    with closing(

        sqlite3.connect(DATABASE_PATH)

    ) as connection:



        students = connection.execute(

            """

            SELECT student_id, wifi_ssid

            FROM students

            WHERE is_active = 1

            """

        ).fetchall()



    results = []



    for student_id, registered_wifi in students:



        registered = (registered_wifi or "").strip()

        visible = bool(

            registered

            and registered.casefold() in available_folded

        )



        results.append(

            {

                "student_id": student_id,

                "registered_wifi": registered_wifi,

                "configured": bool(registered),

                "network_name_visible": visible,

                # Each student has their own hotspot, so a visible

                # hotspot name means their phone is nearby.

                "wifi_present": visible,

            }

        )



    return {

        "evidence_type": "STUDENT_HOTSPOT_SCAN",

        "students": results,

        "checked_at": datetime.now().isoformat(

            timespec="seconds"

        ),

    }





# =========================================================

# WI-FI STATUS FOR ONE STUDENT

# =========================================================



@app.get("/wifi/{student_id}")

def check_wifi_presence(student_id: str):



    with closing(

        sqlite3.connect(DATABASE_PATH)

    ) as connection:



        student = connection.execute(

            """

            SELECT id, name, wifi_ssid

            FROM students

            WHERE student_id = ? COLLATE NOCASE

              AND is_active = 1

            """,

            (student_id,),

        ).fetchone()



    if student is None:



        return {

            "student_id": student_id,

            "wifi_present": False,

            "status": "STUDENT NOT REGISTERED",

        }



    try:

        present = hotspot_visible(student[2])



    except RuntimeError as error:

        raise HTTPException(

            status_code=503,

            detail=str(error),

        )



    registered_wifi = (student[2] or "").strip()



    return {

        "student_id": student_id,

        "name": student[1],

        "registered_wifi": registered_wifi or None,

        "network_name_visible": present,

        "wifi_present": present,

        "status": (

            "HOTSPOT VISIBLE"

            if present

            else "HOTSPOT NOT VISIBLE"

        ),

    }





# =========================================================

# FACE + WI-FI SCAN FOR ONE STUDENT (MARKS ATTENDANCE)

# =========================================================



@app.post("/scan/{student_id}")

def scan_student(

    student_id: str,

    request: ScanRequest,

):



    with closing(

        sqlite3.connect(DATABASE_PATH)

    ) as connection:



        student = connection.execute(

            """

            SELECT student_id, name, wifi_ssid

            FROM students

            WHERE student_id = ? COLLATE NOCASE

              AND is_active = 1

            """,

            (student_id,),

        ).fetchone()



    if student is None:

        raise HTTPException(

            status_code=404,

            detail="Student is not registered.",

        )



    if not is_face_enrolled(student[0]):



        return {

            "student_id": student[0],

            "face_enrolled": False,

            "face_recognized": False,

            "wifi_present": False,

            "attendance_status": "NOT_VERIFIED",

            "attendance_marked": False,

            "message": "This student has no enrolled face profile.",

        }



    try:

        image_bytes = base64.b64decode(

            request.image_base64,

            validate=True,

        )



    except (binascii.Error, ValueError):

        raise HTTPException(

            status_code=400,

            detail="Invalid image data.",

        )



    if (

        not image_bytes

        or len(image_bytes) > 5 * 1024 * 1024

    ):

        raise HTTPException(

            status_code=400,

            detail="Image must be non-empty and no larger than 5MB.",

        )



    recognized_student_id = recognize_face_image(

        image_bytes

    )



    face_recognized = (

        recognized_student_id is not None

        and str(recognized_student_id).casefold()

        == student[0].casefold()

    )



    try:

        wifi_present = hotspot_visible(student[2])



    except RuntimeError as error:

        raise HTTPException(

            status_code=503,

            detail=str(error),

        )



    verified = face_recognized and wifi_present

    marked = False



    if verified:



        with closing(

            sqlite3.connect(

                DATABASE_PATH,

                timeout=10,

            )

        ) as connection:



            marked = mark_attendance_row(

                connection,

                student[0],

                "Present",

            )



            connection.commit()



    if verified:

        message = "Face and hotspot verified. Marked Present."



    elif face_recognized:

        message = (

            "Face recognized, but the student's hotspot is not visible."

        )



    elif wifi_present:

        message = (

            "Hotspot visible, but face was not recognized."

        )



    else:

        message = "Neither face nor hotspot verified."



    return {

        "student_id": student[0],

        "face_enrolled": True,

        "face_recognized": face_recognized,

        "recognized_student_id": recognized_student_id,

        "wifi_present": wifi_present,

        "attendance_status": (

            "PRESENT" if verified else "NOT_VERIFIED"

        ),

        "attendance_marked": marked,

        "message": message,

    }





# =========================================================

# LEGACY ATTENDANCE MARKING - DISABLED

# =========================================================



@app.post("/attendance")

def mark_attendance(student_id: str):



    raise HTTPException(

        status_code=409,

        detail=(

            "Direct attendance marking is disabled. Use /scan/{student_id} "

            "(face + hotspot verification), then /attendance/finalize."

        ),

    )





# =========================================================

# FINALIZE ATTENDANCE - MARK REMAINING STUDENTS ABSENT

# =========================================================



@app.post("/attendance/finalize")

def finalize_attendance():



    absent = []



    with closing(

        sqlite3.connect(

            DATABASE_PATH,

            timeout=10,

        )

    ) as connection:



        connection.execute("BEGIN IMMEDIATE")



        student_ids = [

            row[0]

            for row in connection.execute(

                """

                SELECT student_id

                FROM students

                WHERE is_active = 1

                """

            ).fetchall()

        ]



        for sid in student_ids:



            if mark_attendance_row(

                connection,

                sid,

                "Absent",

            ):

                absent.append(sid)



        connection.commit()



    return {

        "message": "Attendance finalized.",

        "marked_absent": absent,

    }





# =========================================================

# GET ATTENDANCE HISTORY

# =========================================================



@app.get("/attendance")

def get_attendance():



    with closing(

        sqlite3.connect(DATABASE_PATH)

    ) as connection:



        records = connection.execute(

            """

            SELECT

                attendance.id,

                attendance.student_id,

                students.name,

                attendance.date,

                attendance.time,

                attendance.status

            FROM attendance

            LEFT JOIN students

                ON attendance.student_id = students.student_id

            ORDER BY

                attendance.date DESC,

                attendance.time DESC

            """

        ).fetchall()



    return {

        "attendance": records

    }





# =========================================================

# THINGSPEAK TEST

# =========================================================



@app.post("/thingspeak/test")

def test_thingspeak_connection():



    try:



        result = publish_telemetry(

            device_status=1,

            wifi_signal_strength=0,

            face_detection=0,

            recognized_student_count=0,

            attendance_status=0,

            session_status=0,

        )



        return {

            "message": "ThingSpeak connection successful",

            "channel_id": 3578408,

            "thingSpeak": result,

        }



    except RuntimeError as error:

        raise HTTPException(

            status_code=502,

            detail=str(error),

        )