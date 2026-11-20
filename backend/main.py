
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