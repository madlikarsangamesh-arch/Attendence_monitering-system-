
def record_face_observation(session_id, recognized_student_ids, available_ssids=None):

    initialize_session_schema()

    now = timestamp()
    available_ssids = {
        ssid.strip().casefold()
        for ssid in (available_ssids or set())
        if ssid and ssid.strip()
    }

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

        student = connection.execute(
            """
            SELECT ss.student_id, ss.face_first_seen_at,
                   ss.attendance_status, s.wifi_ssid
            FROM session_students ss
            JOIN students s ON s.student_id = ss.student_id
            WHERE ss.session_id = ? AND ss.student_id = ?
            """,
            (session_id, student_id),
        ).fetchone()

        if student is None:
            continue

        registered_ssid = (student["wifi_ssid"] or "").strip().casefold()

        wifi_matches = (
            bool(registered_ssid)
            and registered_ssid in available_ssids
        )

        attendance_status = "PRESENT" if wifi_matches else "ABSENT"

        presence_state = "INSIDE" if wifi_matches else "OUTSIDE"

        connection.execute(
            """
            UPDATE session_students
            SET face_first_seen_at = COALESCE(face_first_seen_at, ?),
                face_last_seen_at = ?,
                first_entry_at = CASE
                    WHEN ? = 'PRESENT'
                    THEN COALESCE(first_entry_at, ?)
                    ELSE first_entry_at
                END,
                presence_state = ?,
                attendance_status = ?
            WHERE session_id = ? AND student_id = ?
            """,
            (
                now,
                now,
                attendance_status,
                now,
                presence_state,
                attendance_status,
                session_id,
                student_id,
            ),
        )

        # Log face recognition only once
        if student["face_first_seen_at"] is None:

            connection.execute(
                """
                INSERT OR IGNORE INTO session_events
                (session_id, student_id, event_type, occurred_at, source, details)
                VALUES (?, ?, 'FACE_RECOGNIZED', ?, 'camera', ?)
                """,
                (
                    session_id,
                    student_id,
                    now,
                    json.dumps({
                        "registered_ssid": student["wifi_ssid"],
                        "ssid_visible": wifi_matches,
                    }),
                ),
            )

        # Log Wi-Fi verification only when result changes
        if student["attendance_status"] != attendance_status:

            event_type = (
                "SSID_MATCH"
                if wifi_matches
                else "SSID_NOT_VISIBLE"
            )

            connection.execute(
                """
                INSERT OR IGNORE INTO session_events
                (session_id, student_id, event_type, occurred_at, source, details)
                VALUES (?, ?, ?, ?, 'wifi_scan', ?)
                """,
                (
                    session_id,
                    student_id,
                    event_type,
                    now,
                    json.dumps({
                        "registered_ssid": student["wifi_ssid"],
                        "ssid_visible": wifi_matches,
                    }),
                ),
            )

        observed.append(student_id)

    connection.commit()
    connection.close()

    if observed:
        _stop_arrival_if_every_enrolled_student_is_verified(session_id)

    return observed
