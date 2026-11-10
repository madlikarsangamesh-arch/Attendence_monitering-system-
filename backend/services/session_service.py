import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from backend.services.device_evidence import get_device_evidence_status
from backend.services.face_service import is_face_enrolled


DATABASE_PATH = Path(__file__).resolve().parents[2] / "Database" / "attendance.db"
DEFAULT_SETTINGS = {
    "arrival_scan_minutes": 10,
    "session_deadline_minutes": 60,
    "disconnect_grace_minutes": 2,
    "minimum_verified_minutes": 45,
}
ACTIVE_STATUSES = ("ARRIVAL_SCANNING", "MONITORING")


def utc_now():

    return datetime.now(timezone.utc)


def timestamp(value=None):

    return (value or utc_now()).isoformat(timespec="milliseconds")


def parse_timestamp(value):

    return datetime.fromisoformat(value)


def connect():

    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_session_schema():

    connection = connect()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS attendance_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance_sessions (
            session_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            arrival_deadline_at TEXT NOT NULL,
            session_deadline_at TEXT NOT NULL,
            stopped_at TEXT,
            settings_json TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_active_attendance_session
        ON attendance_sessions((1))
        WHERE status IN ('ARRIVAL_SCANNING', 'MONITORING');

        CREATE TABLE IF NOT EXISTS session_students (
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            face_first_seen_at TEXT,
            face_last_seen_at TEXT,
            first_entry_at TEXT,
            last_exit_at TEXT,
            disconnect_started_at TEXT,
            last_device_event_at TEXT,
            total_verified_seconds INTEGER NOT NULL DEFAULT 0,
            presence_state TEXT NOT NULL DEFAULT 'NOT_DETECTED',
            attendance_status TEXT NOT NULL DEFAULT 'NOT_DETECTED',
            PRIMARY KEY (session_id, student_id),
            FOREIGN KEY (session_id) REFERENCES attendance_sessions(session_id),
            FOREIGN KEY (student_id) REFERENCES students(student_id)
        );

        CREATE TABLE IF NOT EXISTS presence_intervals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            entered_at TEXT NOT NULL,
            exited_at TEXT,
            verified_seconds INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES attendance_sessions(session_id),
            FOREIGN KEY (student_id) REFERENCES students(student_id)
        );

        CREATE TABLE IF NOT EXISTS session_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL,
            details TEXT,
            provider_event_id TEXT UNIQUE,
            UNIQUE(session_id, source, event_type, student_id, occurred_at),
            FOREIGN KEY (session_id) REFERENCES attendance_sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS student_face_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            image_bytes BLOB NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(student_id)
        );
        """
    )
    student_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(students)")
    }
    if "is_active" not in student_columns:
        connection.execute(
            "ALTER TABLE students ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS students_student_id_case_insensitive
        ON students(student_id COLLATE NOCASE)
        """
    )
    for key, value in DEFAULT_SETTINGS.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO attendance_settings
            (setting_key, setting_value) VALUES (?, ?)
            """,
            (key, value),
        )
    connection.commit()
    connection.close()


def get_settings():

    initialize_session_schema()
    connection = connect()
    rows = connection.execute(
        "SELECT setting_key, setting_value FROM attendance_settings"
    ).fetchall()
    connection.close()

    settings = DEFAULT_SETTINGS.copy()
    settings.update({row["setting_key"]: row["setting_value"] for row in rows})
    return settings


def save_settings(settings):

    initialize_session_schema()
    connection = connect()
    active = connection.execute(
        """
        SELECT session_id FROM attendance_sessions
        WHERE status IN ('ARRIVAL_SCANNING', 'MONITORING')
        """
    ).fetchone()
    if active:
        connection.close()
        raise ValueError("Settings cannot be changed while a session is active.")

    connection.executemany(
        """
        INSERT INTO attendance_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value
        """,
        settings.items(),
    )
    connection.commit()
    connection.close()
    return get_settings()


def start_session():

    initialize_session_schema()
    settings = get_settings()
    now = utc_now()
    arrival_deadline = now + timedelta(minutes=settings["arrival_scan_minutes"])
    deadline = now + timedelta(minutes=settings["session_deadline_minutes"])
    session_id = str(uuid.uuid4())
    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            """
            SELECT session_id FROM attendance_sessions
            WHERE status IN ('ARRIVAL_SCANNING', 'MONITORING')
            """
        ).fetchone()
        if active:
            raise RuntimeError("An attendance session is already active.")

        connection.execute(
            """
            INSERT INTO attendance_sessions
            (session_id, status, started_at, arrival_deadline_at,
             session_deadline_at, settings_json)
            VALUES (?, 'ARRIVAL_SCANNING', ?, ?, ?, ?)
            """,
            (
                session_id,
                timestamp(now),
                timestamp(arrival_deadline),
                timestamp(deadline),
                json.dumps(settings),
            ),
        )
        connection.execute(
            """
            INSERT INTO session_students (session_id, student_id)
            SELECT ?, student_id FROM students WHERE is_active = 1
            """,
            (session_id,),
        )
        connection.execute(
            """
            INSERT INTO session_events
            (session_id, event_type, occurred_at, source, details)
            VALUES (?, 'SESSION_STARTED', ?, 'faculty', ?)
            """,
            (session_id, timestamp(now), json.dumps(settings)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise

    connection.close()
    return get_session_status(session_id)


def record_face_observation(session_id, recognized_student_ids):

    initialize_session_schema()
    now = timestamp()
    connection = connect()
    session = connection.execute(
        """
        SELECT status FROM attendance_sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        connection.close()
        raise LookupError("Attendance session was not found.")
    if session["status"] != "ARRIVAL_SCANNING":
        connection.close()
        return []

    observed = []
    for student_id in set(recognized_student_ids):
        existing = connection.execute(
            """
            SELECT face_first_seen_at, presence_state
            FROM session_students
            WHERE session_id = ? AND student_id = ?
            """,
            (session_id, student_id),
        ).fetchone()
        if existing is None:
            continue
        cursor = connection.execute(
            """
            UPDATE session_students
            SET face_first_seen_at = COALESCE(face_first_seen_at, ?),
                face_last_seen_at = ?,
                attendance_status = CASE
                    WHEN presence_state = 'INSIDE'
                    THEN 'PRESENT'
                    ELSE attendance_status
                END
            WHERE session_id = ? AND student_id = ?
            """,
            (now, now, session_id, student_id),
        )
        if cursor.rowcount and existing["face_first_seen_at"] is None:
            connection.execute(
                """
                INSERT OR IGNORE INTO session_events
                (session_id, student_id, event_type, occurred_at, source)
                VALUES (?, ?, 'FACE_RECOGNIZED', ?, 'camera')
                """,
                (session_id, student_id, now),
            )
            observed.append(student_id)
    connection.commit()
    connection.close()
    if observed:
        _stop_arrival_if_every_enrolled_student_is_verified(session_id)
    return observed


def get_session_status(session_id=None):

    connection = connect()
    if session_id is None:
        session = connection.execute(
            """
            SELECT * FROM attendance_sessions
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
    else:
        session = connection.execute(
            "SELECT * FROM attendance_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    if session is None:
        registered_count = connection.execute(
            "SELECT COUNT(*) AS count FROM students"
        ).fetchone()["count"]
        connection.close()
        return {
            "session": None,
            "registered_students": registered_count,
            "present_students": 0,
            "not_yet_detected": registered_count,
            "left_early": 0,
            "currently_inside": 0,
            "students": [],
            "device_evidence": get_device_evidence_status(),
        }

    students = connection.execute(
        """
        SELECT ss.*, s.name
        FROM session_students ss
        JOIN students s ON s.student_id = ss.student_id
        WHERE ss.session_id = ?
        ORDER BY s.name, s.student_id
        """,
        (session["session_id"],),
    ).fetchall()

    current_time = utc_now()
    arrival_deadline = parse_timestamp(session["arrival_deadline_at"])
    deadline = parse_timestamp(session["session_deadline_at"])
    arrival_remaining = max(0, int((arrival_deadline - current_time).total_seconds()))
    session_remaining = max(0, int((deadline - current_time).total_seconds()))
    student_rows = [dict(student) for student in students]
    open_intervals = connection.execute(
        """
        SELECT pi.student_id, pi.entered_at, ss.disconnect_started_at
        FROM presence_intervals pi
        JOIN session_students ss
          ON ss.session_id = pi.session_id
         AND ss.student_id = pi.student_id
        WHERE pi.session_id = ? AND pi.exited_at IS NULL
        """,
        (session["session_id"],),
    ).fetchall()
    open_duration = {
        row["student_id"]: max(
            0,
            int(
                (
                    (
                        parse_timestamp(row["disconnect_started_at"])
                        if row["disconnect_started_at"]
                        else current_time
                    )
                    - parse_timestamp(row["entered_at"])
                ).total_seconds()
            ),
        )
        for row in open_intervals
    }
    connection.close()
    for student in student_rows:
        student["face_enrolled"] = is_face_enrolled(student["student_id"])
        student["live_verified_seconds"] = (
            student["total_verified_seconds"]
            + open_duration.get(student["student_id"], 0)
        )
        if not student["face_enrolled"]:
            student["face_status"] = "NOT_ENROLLED"
        elif student["face_first_seen_at"] and not student["first_entry_at"]:
            student["face_status"] = "FACE_DETECTED_AWAITING_DEVICE"
        elif student["face_first_seen_at"] is None:
            student["face_status"] = "FACE_NOT_DETECTED"
        else:
            student["face_status"] = "VERIFIED"

    return {
        "session": {
            **dict(session),
            "arrival_seconds_remaining": arrival_remaining,
            "session_seconds_remaining": session_remaining,
            "device_monitoring": "UNAVAILABLE",
        },
        "registered_students": len(student_rows),
        "present_students": sum(
            student["attendance_status"] == "PRESENT"
            for student in student_rows
        ),
        "not_yet_detected": sum(
            student["face_first_seen_at"] is None
            for student in student_rows
        ),
        "left_early": sum(
            student["attendance_status"] == "LEFT_EARLY"
            for student in student_rows
        ),
        "currently_inside": sum(
            student["presence_state"] in {"INSIDE", "DISCONNECT_GRACE"}
            and student["attendance_status"] == "PRESENT"
            for student in student_rows
        ),
        "students": student_rows,
        "device_evidence": get_device_evidence_status(),
    }


def _close_open_intervals(connection, session_id, stopped_at):

    intervals = connection.execute(
        """
        SELECT id, student_id, entered_at
        FROM presence_intervals
        WHERE session_id = ? AND exited_at IS NULL
        """,
        (session_id,),
    ).fetchall()
    for interval in intervals:
        student_state = connection.execute(
            """
            SELECT disconnect_started_at
            FROM session_students
            WHERE session_id = ? AND student_id = ?
            """,
            (session_id, interval["student_id"]),
        ).fetchone()
        exit_at = (
            student_state["disconnect_started_at"]
            if student_state and student_state["disconnect_started_at"]
            else stopped_at
        )
        seconds = max(
            0,
            int(
                (
                    parse_timestamp(exit_at)
                    - parse_timestamp(interval["entered_at"])
                ).total_seconds()
            ),
        )
        connection.execute(
            """
            UPDATE presence_intervals
            SET exited_at = ?, verified_seconds = ?
            WHERE id = ?
            """,
            (exit_at, seconds, interval["id"]),
        )
        connection.execute(
            """
            UPDATE session_students
            SET total_verified_seconds = total_verified_seconds + ?,
                last_exit_at = ?, presence_state = 'OUTSIDE'
            WHERE session_id = ? AND student_id = ?
            """,
            (seconds, exit_at, session_id, interval["student_id"]),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO session_events
            (session_id, student_id, event_type, occurred_at, source)
            VALUES (?, ?, 'SESSION_CLOSED_INTERVAL', ?, 'session')
            """,
            (session_id, interval["student_id"], stopped_at),
        )


def _finalize_session(connection, session, status, stopped_at):

    import json

    _close_open_intervals(connection, session["session_id"], stopped_at)
    settings = json.loads(session["settings_json"])
    students = connection.execute(
        """
        SELECT student_id, first_entry_at, total_verified_seconds
        FROM session_students
        WHERE session_id = ?
        """,
        (session["session_id"],),
    ).fetchall()

    for student in students:
        if student["first_entry_at"] is None:
            attendance_status = "NOT_VERIFIED"
        elif student["total_verified_seconds"] < (
            settings["minimum_verified_minutes"] * 60
        ):
            attendance_status = "LEFT_EARLY"
        else:
            attendance_status = "PRESENT"
        connection.execute(
            """
            UPDATE session_students
            SET attendance_status = ?
            WHERE session_id = ? AND student_id = ?
            """,
            (attendance_status, session["session_id"], student["student_id"]),
        )

    connection.execute(
        """
        UPDATE attendance_sessions
        SET status = ?, stopped_at = ?
        WHERE session_id = ?
        """,
        (status, stopped_at, session["session_id"]),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO session_events
        (session_id, event_type, occurred_at, source)
        VALUES (?, ?, ?, 'session')
        """,
        (session["session_id"], status, stopped_at),
    )


def stop_session(session_id, reason="STOPPED"):

    initialize_session_schema()
    stopped_at = timestamp()
    connection = connect()
    session = connection.execute(
        "SELECT * FROM attendance_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if session is None:
        connection.close()
        raise LookupError("Attendance session was not found.")
    if session["status"] in ACTIVE_STATUSES:
        _finalize_session(connection, session, reason, stopped_at)
        connection.commit()
    connection.close()
    return get_session_status(session_id)


def process_session_deadlines():

    now = utc_now()
    now_string = timestamp(now)
    connection = connect()
    sessions = connection.execute(
        """
        SELECT * FROM attendance_sessions
        WHERE status IN ('ARRIVAL_SCANNING', 'MONITORING')
        """
    ).fetchall()
    for session in sessions:
        deadline = parse_timestamp(session["session_deadline_at"])
        grace_seconds = (
            json.loads(session["settings_json"])["disconnect_grace_minutes"] * 60
        )
        pending_disconnects = connection.execute(
            """
            SELECT student_id, disconnect_started_at
            FROM session_students
            WHERE session_id = ? AND presence_state = 'DISCONNECT_GRACE'
            """,
            (session["session_id"],),
        ).fetchall()
        for pending in pending_disconnects:
            disconnected_at = parse_timestamp(pending["disconnect_started_at"])
            if (
                now >= disconnected_at + timedelta(seconds=grace_seconds)
                or now >= deadline
            ):
                _confirm_exit(
                    connection,
                    session["session_id"],
                    pending["student_id"],
                    pending["disconnect_started_at"],
                )

        if now >= deadline:
            _finalize_session(
                connection, session, "COMPLETED", now_string
            )
            continue

        if (
            session["status"] == "ARRIVAL_SCANNING"
            and now >= parse_timestamp(session["arrival_deadline_at"])
        ):
            connection.execute(
                """
                UPDATE attendance_sessions
                SET status = 'MONITORING'
                WHERE session_id = ?
                """,
                (session["session_id"],),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO session_events
                (session_id, event_type, occurred_at, source)
                VALUES (?, 'ARRIVAL_SCAN_ENDED', ?, 'session')
                """,
                (session["session_id"], now_string),
            )
    connection.commit()
    connection.close()


def _confirm_exit(connection, session_id, student_id, disconnected_at):

    interval = connection.execute(
        """
        SELECT id, entered_at FROM presence_intervals
        WHERE session_id = ? AND student_id = ? AND exited_at IS NULL
        """,
        (session_id, student_id),
    ).fetchone()
    if interval is None:
        return

    seconds = max(
        0,
        int(
            (
                parse_timestamp(disconnected_at)
                - parse_timestamp(interval["entered_at"])
            ).total_seconds()
        ),
    )
    connection.execute(
        """
        UPDATE presence_intervals
        SET exited_at = ?, verified_seconds = ?
        WHERE id = ?
        """,
        (disconnected_at, seconds, interval["id"]),
    )
    connection.execute(
        """
        UPDATE session_students
        SET total_verified_seconds = total_verified_seconds + ?,
            last_exit_at = ?, disconnect_started_at = NULL,
            presence_state = 'OUTSIDE'
        WHERE session_id = ? AND student_id = ?
        """,
        (seconds, disconnected_at, session_id, student_id),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO session_events
        (session_id, student_id, event_type, occurred_at, source)
        VALUES (?, ?, 'EXIT_CONFIRMED', ?, 'authenticated_device')
        """,
        (session_id, student_id, timestamp()),
    )


def apply_authenticated_device_event(
    session_id,
    student_id,
    event_type,
    occurred_at,
    provider_event_id,
):

    initialize_session_schema()
    if event_type not in {"CONNECTED", "DISCONNECTED"}:
        raise ValueError("Unsupported authenticated device event.")
    event_time = parse_timestamp(occurred_at)
    if event_time.tzinfo is None:
        raise ValueError("Device event timestamps must include a timezone.")
    if not provider_event_id:
        raise ValueError("Device events must include a provider event ID.")

    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            "SELECT status FROM attendance_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        student = connection.execute(
            """
            SELECT * FROM session_students
            WHERE session_id = ? AND student_id = ?
            """,
            (session_id, student_id),
        ).fetchone()
        if session is None or session["status"] not in ACTIVE_STATUSES:
            raise LookupError("No active session accepts device events.")
        if student is None:
            raise LookupError("Student is not part of this session.")
        session_started = connection.execute(
            "SELECT started_at FROM attendance_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()["started_at"]
        if event_time < parse_timestamp(session_started):
            raise ValueError("Device event predates the attendance session.")
        if (
            student["last_device_event_at"]
            and event_time <= parse_timestamp(student["last_device_event_at"])
        ):
            connection.rollback()
            connection.close()
            return get_session_status(session_id)
        if connection.execute(
            "SELECT 1 FROM session_events WHERE provider_event_id = ?",
            (provider_event_id,),
        ).fetchone():
            connection.rollback()
            connection.close()
            return get_session_status(session_id)

        connection.execute(
            """
            INSERT INTO session_events
            (session_id, student_id, event_type, occurred_at, source,
             provider_event_id)
            VALUES (?, ?, ?, ?, 'authenticated_device', ?)
            """,
            (
                session_id,
                student_id,
                event_type,
                timestamp(event_time),
                provider_event_id,
            ),
        )

        if event_type == "CONNECTED":
            if student["presence_state"] == "DISCONNECT_GRACE":
                connection.execute(
                    """
                    UPDATE session_students
                    SET presence_state = 'INSIDE',
                        disconnect_started_at = NULL,
                        last_device_event_at = ?
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (timestamp(event_time), session_id, student_id),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO session_events
                    (session_id, student_id, event_type, occurred_at, source)
                    VALUES (?, ?, 'RECONNECTED_DURING_GRACE', ?,
                            'authenticated_device')
                    """,
                    (session_id, student_id, timestamp(event_time)),
                )
            elif student["presence_state"] != "INSIDE":
                connection.execute(
                    """
                    INSERT INTO presence_intervals
                    (session_id, student_id, entered_at)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, student_id, timestamp(event_time)),
                )
                connection.execute(
                    """
                    UPDATE session_students
                    SET presence_state = 'INSIDE',
                        first_entry_at = COALESCE(first_entry_at, ?),
                        last_device_event_at = ?,
                        attendance_status = CASE
                            WHEN face_first_seen_at IS NOT NULL
                            THEN 'PRESENT'
                            ELSE 'AWAITING_FACE'
                        END
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (
                        timestamp(event_time),
                        timestamp(event_time),
                        session_id,
                        student_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE session_students
                    SET last_device_event_at = ?
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (timestamp(event_time), session_id, student_id),
                )
        elif student["presence_state"] == "INSIDE":
            connection.execute(
                """
                UPDATE session_students
                SET presence_state = 'DISCONNECT_GRACE',
                    disconnect_started_at = ?,
                    last_device_event_at = ?
                WHERE session_id = ? AND student_id = ?
                """,
                (
                    timestamp(event_time),
                    timestamp(event_time),
                    session_id,
                    student_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE session_students
                SET last_device_event_at = ?
                WHERE session_id = ? AND student_id = ?
                """,
                (timestamp(event_time), session_id, student_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise

    connection.close()
    process_session_deadlines()

    if event_type == "CONNECTED":
        _stop_arrival_if_every_enrolled_student_is_verified(session_id)
    return get_session_status(session_id)


def _stop_arrival_if_every_enrolled_student_is_verified(session_id):

    connection = connect()
    session = connection.execute(
        "SELECT status FROM attendance_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if session is None or session["status"] != "ARRIVAL_SCANNING":
        connection.close()
        return

    registered_ids = [
        row["student_id"]
        for row in connection.execute(
            "SELECT student_id FROM session_students WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    ]
    enrolled_ids = [
        student_id
        for student_id in registered_ids
        if is_face_enrolled(student_id)
    ]
    if not enrolled_ids:
        connection.close()
        return

    verified_count = connection.execute(
        """
        SELECT COUNT(*) AS count FROM session_students
        WHERE session_id = ? AND attendance_status = 'PRESENT'
        """,
        (session_id,),
    ).fetchone()["count"]
    if verified_count >= len(enrolled_ids):
        connection.execute(
            """
            UPDATE attendance_sessions
            SET status = 'MONITORING'
            WHERE session_id = ?
            """,
            (session_id,),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO session_events
            (session_id, event_type, occurred_at, source)
            VALUES (?, 'ARRIVAL_SCAN_ENDED', ?, 'session')
            """,
            (session_id, timestamp()),
        )
        connection.commit()
    connection.close()


async def session_deadline_watcher():

    initialize_session_schema()
    while True:
        process_session_deadlines()
        await asyncio.sleep(1)
