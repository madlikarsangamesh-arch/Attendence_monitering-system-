import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np


# =========================================================
# PROJECT PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parents[2]

MODEL_PATH = os.path.join(
    BASE_DIR,
    "backend",
    "trainer.yml"
)

CASCADE_PATH = os.path.join(
    BASE_DIR,
    "backend",
    "haarcascade_frontalface_default.xml"
)
DATABASE_PATH = BASE_DIR / "Database" / "attendance.db"
DATASET_PATH = BASE_DIR / "dataset"


# =========================================================
# CONFIGURATION
# =========================================================

RECOGNITION_THRESHOLD = 65
FACE_SIZE = (160, 160)


# =========================================================
# LOAD FACE DETECTOR
# =========================================================

face_cascade = cv2.CascadeClassifier(
    CASCADE_PATH
)
_recognizer = None
_label_to_student = {}
_model_signature = None
_model_lock = threading.Lock()


def _decode_faces(image_bytes):

    if face_cascade.empty():
        raise RuntimeError("Face detector could not be loaded.")

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("The uploaded image could not be decoded.")

    detected_faces = face_cascade.detectMultiScale(
        image,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(80, 80),
    )
    return [
        cv2.resize(image[y:y + height, x:x + width], FACE_SIZE)
        for x, y, width, height in detected_faces
    ]


def validate_face_enrollment_image(image_bytes):

    faces = _decode_faces(image_bytes)
    if not faces:
        raise ValueError("No face was detected in the enrollment photo.")
    if len(faces) > 1:
        raise ValueError("Use an enrollment photo with only one visible face.")


def invalidate_face_model():

    global _recognizer, _label_to_student, _model_signature
    with _model_lock:
        _recognizer = None
        _label_to_student = {}
        _model_signature = None


def _sample_signature():

    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(id), 0)
                FROM student_face_samples
                """
            ).fetchone()
            return row
    except sqlite3.OperationalError:
        return (0, 0)


def _read_training_samples():

    samples_by_student = {}
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            rows = connection.execute(
                """
                SELECT student_id, image_bytes
                FROM student_face_samples
                ORDER BY id
                """
            ).fetchall()
            student_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT student_id FROM students"
                ).fetchall()
            ]
    except sqlite3.OperationalError:
        rows = []
        student_ids = []

    for student_id, image_bytes in rows:
        try:
            faces = _decode_faces(image_bytes)
        except ValueError:
            continue
        samples_by_student.setdefault(student_id, []).extend(faces)

    if DATASET_PATH.is_dir():
        legacy_images = [
            path
            for path in DATASET_PATH.iterdir()
            if path.is_file()
            and path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
        ]
        for student_id in student_ids:
            for path in legacy_images:
                if path.name.casefold().startswith(student_id.casefold()):
                    image_bytes = path.read_bytes()
                    try:
                        faces = _decode_faces(image_bytes)
                    except ValueError:
                        continue
                    samples_by_student.setdefault(student_id, []).extend(faces)

    return samples_by_student


def _load_recognizer():

    global _recognizer, _label_to_student, _model_signature
    signature = _sample_signature()
    if _recognizer is not None and signature == _model_signature:
        return _recognizer, _label_to_student

    with _model_lock:
        signature = _sample_signature()
        if _recognizer is not None and signature == _model_signature:
            return _recognizer, _label_to_student

        samples_by_student = _read_training_samples()
        ordered_ids = sorted(samples_by_student)
        faces = []
        labels = []
        label_to_student = {}
        for label, student_id in enumerate(ordered_ids, start=1):
            label_to_student[label] = student_id
            for face in samples_by_student[student_id]:
                faces.append(face)
                labels.append(label)

        if not faces:
            _recognizer = None
            _label_to_student = {}
            _model_signature = signature
            return None, {}

        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.train(
            faces,
            np.asarray(labels, dtype=np.int32),
        )
        _recognizer = recognizer
        _label_to_student = label_to_student
        _model_signature = signature
        return _recognizer, _label_to_student


def is_face_enrolled(student_id):

    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM student_face_samples
                WHERE student_id = ? COLLATE NOCASE
                LIMIT 1
                """,
                (student_id,),
            ).fetchone()
            if row:
                return True
    except sqlite3.OperationalError:
        pass

    return DATASET_PATH.is_dir() and any(
        path.is_file()
        and path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
        and path.name.casefold().startswith(student_id.casefold())
        for path in DATASET_PATH.iterdir()
    )


def recognize_face_image(image_bytes):

    faces = _decode_faces(image_bytes)
    if not faces:
        return None

    recognizer, label_to_student = _load_recognizer()
    if recognizer is None:
        return None

    best_match = None
    for face in faces:
        label, confidence = recognizer.predict(face)

        if confidence < RECOGNITION_THRESHOLD and (
            best_match is None or confidence < best_match[0]
        ):
            student_id = label_to_student.get(label)
            if student_id is not None:
                best_match = (confidence, student_id)

    return best_match[1] if best_match else None


# =========================================================
# RECOGNIZE FACE FROM CAMERA
# =========================================================

def recognize_face_camera():

    if face_cascade.empty():

        print("ERROR: Face detector could not be loaded.")

        return None


    camera = cv2.VideoCapture(0)


    if not camera.isOpened():

        print("ERROR: Could not open laptop camera.")

        return None


    print()
    print("======================================")
    print("LIVE FACE RECOGNITION")
    print("======================================")
    print("Look at the camera.")
    print("Press Q to cancel.")
    print()


    recognized_student = None


    while True:

        success, frame = camera.read()


        if not success:

            print("ERROR: Could not read camera frame.")

            break


        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )


        detected_faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(80, 80)
        )


        for (x, y, width, height) in detected_faces:

            face = gray[
                y:y + height,
                x:x + width
            ]


            recognized_student_id = recognize_face_image(
                cv2.imencode(".jpg", face)[1].tobytes()
            )

            if recognized_student_id is not None:

                recognized_student = recognized_student_id

                text = f"{recognized_student_id} - Recognized"

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + width, y + height),
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    frame,
                    text,
                    (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2
                )


            else:

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + width, y + height),
                    (0, 0, 255),
                    2
                )

                cv2.putText(
                    frame,
                    "Unknown",
                    (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2
                )


        cv2.imshow(
            "AttendAIoT - Face Recognition",
            frame
        )


        if recognized_student is not None:

            cv2.waitKey(1000)

            break


        if cv2.waitKey(1) & 0xFF == ord("q"):

            break


    camera.release()

    cv2.destroyAllWindows()


    return recognized_student