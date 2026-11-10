import cv2
import os


# =========================================================
# PATHS
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

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


STUDENT_ID = "20241ise0219"

RECOGNITION_THRESHOLD = 50


# =========================================================
# LOAD FACE DETECTOR
# =========================================================

face_cascade = cv2.CascadeClassifier(
    CASCADE_PATH
)

if face_cascade.empty():

    print("ERROR: Face detector could not be loaded.")

    exit()


# =========================================================
# LOAD FACE RECOGNITION MODEL
# =========================================================

recognizer = cv2.face.LBPHFaceRecognizer_create()

recognizer.read(MODEL_PATH)

print("Face recognition model loaded successfully.")
print()
print("Starting laptop camera...")
print("Press Q to quit.")
print()


# =========================================================
# OPEN LAPTOP CAMERA
# =========================================================

camera = cv2.VideoCapture(0)


if not camera.isOpened():

    print("ERROR: Could not open laptop camera.")

    exit()


# =========================================================
# LIVE CAMERA LOOP
# =========================================================

while True:

    success, frame = camera.read()

    if not success:

        print("ERROR: Could not read camera frame.")

        break


    # Convert frame to grayscale
    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY
    )


    # Detect faces
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


        # Predict identity
        label, confidence = recognizer.predict(face)


        # Draw rectangle around face
        cv2.rectangle(
            frame,
            (x, y),
            (x + width, y + height),
            (0, 255, 0),
            2
        )


        # =================================================
        # RECOGNITION RESULT
        # =================================================

        if (
            label == 1
            and confidence < RECOGNITION_THRESHOLD
        ):

            text = "Sangamesh - Recognized"

            cv2.putText(
                frame,
                text,
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            print(
                "Recognized:",
                STUDENT_ID,
                "| LBPH distance:",
                confidence
            )

        else:

            text = "Unknown"

            cv2.putText(
                frame,
                text,
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2
            )


    # Display camera
    cv2.imshow(
        "AttendAIoT - Face Recognition",
        frame
    )


    # Press Q to quit
    if cv2.waitKey(1) & 0xFF == ord("q"):

        break


# =========================================================
# CLEANUP
# =========================================================

camera.release()

cv2.destroyAllWindows()

print()
print("Camera test ended.")