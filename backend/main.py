import asyncio
import base64
import binascii
import sqlite3
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


app = FastAPI(title="AttendAIoT API", lifespan=lifespan)
DATABASE_PATH = Path(__file__).resolve().parents[1] / "Database" / "attendance.db"


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


@app.get("/session-settings")
def read_session_settings():

    return get_settings()


@app.put("/session-settings")
def update_session_settings(settings: SessionSettings):

    if settings.arrival_scan_minutes > settings.session_deadline_minutes:
        raise HTTPException(
            status_code=422,
            detail="Arrival scanning cannot outlast the session deadline.",
        )
    if settings.minimum_verified_minutes > settings.session_deadline_minutes:
        raise HTTPException(
            status_code=422,
            detail="Minimum verified attendance cannot exceed the session deadline.",
        )

    try:
        saved = save_settings(settings.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))

    return {"settings": saved}


@app.get("/device-evidence/status")
def device_evidence_status():

    return get_device_evidence_status()


@app.post("/sessions")
def create_attendance_session():

    try:
        return start_session()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))


@app.get("/sessions/current")
def current_attendance_session():

    return get_session_status()


@app.get("/sessions/{session_id}")
def attendance_session(session_id: str):

    result = get_session_status(session_id)
    if result["session"] is None:
        raise HTTPException(status_code=404, detail="Attendance session not found.")
    return result


@app.post("/sessions/{session_id}/stop")
def stop_attendance_session(session_id: str):

    try:
        return stop_session(session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))


@app.post("/sessions/{session_id}/frame")
def process_session_frame(session_id: str, request: SessionFrameRequest):

    try:
        image_bytes = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid image data.")

    if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Image must be non-empty and no larger than 5MB.",
        )

    try:
        status = get_session_status(session_id)
        if status["session"] is None:
            raise HTTPException(status_code=404, detail="Attendance session not found.")
        if status["session"]["status"] != "ARRIVAL_SCANNING":
            return {
                "recognized_student_ids": [],
                "arrival_scanning": False,
                "message": "Arrival scanning is not active.",
            }

        recognized_student_id = recognize_face_image(image_bytes)
        recognized_ids = [recognized_student_id] if recognized_student_id else []
        observed_ids = record_face_observation(session_id, recognized_ids)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))

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
# REGISTER STUDENT
# =========================================================

@app.post("/students")
def add_student(
    student_id: str | None = None,
    name: str | None = None,
    wifi_ssid: str | None = None,
    request: StudentRegistrationRequest | None = None,
):

    student_id = (request.student_id if request else student_id or "").strip()
    name = (request.name if request else name or "").strip()
    wifi_ssid = (request.wifi_ssid if request else wifi_ssid or "").strip()
    if not student_id or not name or not wifi_ssid:
        raise HTTPException(
            status_code=422,
            detail="Student ID, name, and Wi-Fi SSID are required.",
        )

    image_bytes = None
    image_base64 = request.image_base64 if request else None
    if image_base64:
        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid face image data.")
        if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="Face image must be non-empty and no larger than 5MB.",
            )

    initialize_session_schema()
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT student_id, name
            FROM students
            WHERE student_id = ? COLLATE NOCASE
            """,
            (student_id,),
        ).fetchone()
        if existing:
            connection.rollback()
            return {
                "message": "Student is already registered; no duplicate was added.",
                "student_id": existing[0],
                "name": existing[1],
                "already_exists": True,
            }

        if image_bytes:
            try:
                validate_face_enrollment_image(image_bytes)
            except ValueError as error:
                connection.rollback()
                raise HTTPException(status_code=422, detail=str(error))

        connection.execute(
            """
            INSERT INTO students (student_id, name, wifi_ssid)
            VALUES (?, ?, ?)
            """,
            (student_id, name, wifi_ssid),
        )
        if image_bytes:
            connection.execute(
                """
                INSERT INTO student_face_samples (student_id, image_bytes, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    student_id,
                    image_bytes,
                    datetime.now().astimezone().isoformat(timespec="milliseconds"),
                ),
            )
        connection.commit()

    if image_bytes:
        invalidate_face_model()

    return {
        "message": (
            "Student and face profile saved successfully."
            if image_bytes
            else "Student added successfully; no face image was enrolled."
        ),
        "student_id": student_id,
        "name": name,
        "wifi_ssid": wifi_ssid,
        "already_exists": False,
        "face_enrolled": bool(image_bytes),
    }


# =========================================================
# GET ALL STUDENTS
# =========================================================

@app.get("/students")
def get_students():

    connection = sqlite3.connect(
        DATABASE_PATH
    )

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
        (*student, is_face_enrolled(student[1]))
        for student in cursor.fetchall()
    ]

    connection.close()

    return {
        "students": students
    }


@app.put("/students/{student_id}")
def update_student(student_id: str, request: StudentUpdateRequest):

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
            image_bytes = base64.b64decode(request.image_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid face image data.")
        if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="Face image must be non-empty and no larger than 5MB.",
            )
        try:
            validate_face_enrollment_image(image_bytes)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))

    initialize_session_schema()
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        student = connection.execute(
            """
            SELECT student_id FROM students
            WHERE student_id = ? COLLATE NOCASE AND is_active = 1
            """,
            (student_id,),
        ).fetchone()
        if student is None:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Student is not registered.")

        in_active_session = connection.execute(
            """
            SELECT 1
            FROM session_students ss
            JOIN attendance_sessions sessions
              ON sessions.session_id = ss.session_id
            WHERE ss.student_id = ?
              AND sessions.status IN ('ARRIVAL_SCANNING', 'MONITORING')
            LIMIT 1
            """,
            (student[0],),
        ).fetchone()
        if in_active_session:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="This student is part of the active attendance session. Stop that session before editing their details.",
            )

        connection.execute(
            """
            UPDATE students SET name = ?, wifi_ssid = ?
            WHERE student_id = ? COLLATE NOCASE
            """,
            (name, wifi_ssid, student[0]),
        )
        if image_bytes:
            connection.execute(
                """
                INSERT INTO student_face_samples (student_id, image_bytes, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    student[0],
                    image_bytes,
                    datetime.now().astimezone().isoformat(timespec="milliseconds"),
                ),
            )
        connection.commit()

    if image_bytes:
        invalidate_face_model()
    return {
        "message": "Student details and face profile updated."
        if image_bytes
        else "Student details updated.",
        "student_id": student[0],
        "name": name,
        "wifi_ssid": wifi_ssid,
        "face_updated": bool(image_bytes),
    }


@app.delete("/students/{student_id}")
def archive_student(student_id: str):

    initialize_session_schema()
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        student = connection.execute(
            """
            SELECT student_id FROM students
            WHERE student_id = ? COLLATE NOCASE AND is_active = 1
            """,
            (student_id,),
        ).fetchone()
        if student is None:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Student is not registered.")

        in_active_session = connection.execute(
            """
            SELECT 1
            FROM session_students ss
            JOIN attendance_sessions sessions
              ON sessions.session_id = ss.session_id
            WHERE ss.student_id = ?
              AND sessions.status IN ('ARRIVAL_SCANNING', 'MONITORING')
            LIMIT 1
            """,
            (student[0],),
        ).fetchone()
        if in_active_session:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="This student is part of the active attendance session. Stop that session before removing them.",
            )

        connection.execute(
            "UPDATE students SET is_active = 0 WHERE student_id = ? COLLATE NOCASE",
            (student[0],),
        )
        connection.commit()

    return {
        "message": "Student archived from the active roster. Attendance history was preserved.",
        "student_id": student[0],
    }


@app.get("/wifi/status")
def get_wifi_status():

    try:
        available_ssids = scan_wifi_ssids()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error))
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        students = connection.execute(
            """
            SELECT student_id, wifi_ssid FROM students
            WHERE is_active = 1
            """
        ).fetchall()

    return {
        "evidence_type": "ACCESS_POINT_SCAN",
        "individual_device_presence_available": False,
        "scan_result": "ACCESS_POINT_NAMES_ONLY",
        "students": [
            {
                "student_id": student_id,
                "registered_wifi": registered_wifi,
                "configured": bool(registered_wifi and registered_wifi.strip()),
                "network_name_visible": bool(
                    registered_wifi
                    and registered_wifi.strip()
                    and any(
                        ssid.casefold() == registered_wifi.strip().casefold()
                        for ssid in available_ssids
                    )
                ),
                "wifi_present": False,
            }
            for student_id, registered_wifi in students
        ],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


# =========================================================
# CHECK WIFI PRESENCE
# =========================================================

@app.get("/wifi/{student_id}")
def check_wifi_presence(student_id: str):

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT id, name, wifi_ssid
            FROM students
            WHERE student_id = ? COLLATE NOCASE AND is_active = 1
            """,
            (student_id,)
        )
        student = cursor.fetchone()
    finally:
        connection.close()

    if student is None:
        return {
            "student_id": student_id,
            "wifi_present": False,
            "status": "STUDENT NOT REGISTERED"
        }

    try:
        available_ssids = scan_wifi_ssids()
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

    registered_wifi = (student[2] or "").strip()

    network_name_visible = bool(
        registered_wifi
        and any(
            ssid.casefold() == registered_wifi.casefold()
            for ssid in available_ssids
        )
    )

    return {
        "student_id": student_id,
        "name": student[1],
        "registered_wifi": registered_wifi or None,
        "network_name_visible": network_name_visible,
        "individual_device_presence_available": False,
        "wifi_present": False,
        "status": (
            "REGISTERED NETWORK NAME VISIBLE (NOT DEVICE PRESENCE)"
            if network_name_visible
            else "REGISTERED NETWORK NAME NOT VISIBLE (NOT DEVICE STATUS)"
        )
    }

@app.post("/scan/{student_id}")
def scan_student(student_id: str, request: ScanRequest):

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT student_id, name, wifi_ssid
        FROM students
        WHERE student_id = ? COLLATE NOCASE AND is_active = 1
        """,
        (student_id,)
    )
    student = cursor.fetchone()
    connection.close()

    if student is None:
        raise HTTPException(status_code=404, detail="Student is not registered.")

    if not is_face_enrolled(student_id):
        return {
            "student_id": student_id,
            "face_enrolled": False,
            "face_recognized": False,
            "attendance_status": "NOT_VERIFIED",
            "message": "This student has no enrolled face profile.",
        }

    try:
        image_bytes = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid image data.")

    if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Image must be non-empty and no larger than 5MB.",
        )

    recognized_student_id = recognize_face_image(image_bytes)
    face_recognized = recognized_student_id == student_id

    return {
        "student_id": student_id,
        "face_enrolled": True,
        "face_recognized": face_recognized,
        "recognized_student_id": recognized_student_id,
        "attendance_status": (
            "AWAITING_AUTHENTICATED_DEVICE"
            if face_recognized
            else "NOT_VERIFIED"
        ),
        "attendance_marked": False,
        "message": (
            "Face recognized. Attendance remains unverified until "
            "authenticated device evidence is available."
            if face_recognized
            else "Face was not recognized. Retry the scan."
        ),
    }


# =========================================================
# MARK ATTENDANCE
# =========================================================

@app.post("/attendance")
def mark_attendance(student_id: str):
    raise HTTPException(
        status_code=409,
        detail=(
            "Legacy attendance marking is disabled. Start an attendance session; "
            "attendance requires both an enrolled face and authenticated device evidence."
        ),
    )


# =========================================================
# GET ATTENDANCE WITH STUDENT NAME
# =========================================================

@app.get("/attendance")
def get_attendance():

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
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
        ORDER BY attendance.date DESC,
                 attendance.time DESC
        """
    )

    records = cursor.fetchall()

    connection.close()

    return {
        "attendance": records
    }