import cv2
import os


# --------------------------------------------------
# PROJECT PATH
# --------------------------------------------------

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

TEST_IMAGE_PATH = os.path.join(
    BASE_DIR,
    "test_images",
    "20241ise0219_test.jpg"
)


# --------------------------------------------------
# RECOGNITION THRESHOLD
# --------------------------------------------------

RECOGNITION_THRESHOLD = 50


# --------------------------------------------------
# LOAD FACE DETECTOR
# --------------------------------------------------

face_cascade = cv2.CascadeClassifier(
    CASCADE_PATH
)

if face_cascade.empty():
    print("ERROR: Face detector could not be loaded.")
    exit()


# --------------------------------------------------
# LOAD TRAINED MODEL
# --------------------------------------------------

recognizer = cv2.face.LBPHFaceRecognizer_create()

recognizer.read(
    MODEL_PATH
)

print("Face recognition model loaded successfully.")
print()


# --------------------------------------------------
# TEST IMAGE
# --------------------------------------------------

print("Testing image:")
print(TEST_IMAGE_PATH)
print()


# --------------------------------------------------
# READ IMAGE
# --------------------------------------------------

image = cv2.imread(
    TEST_IMAGE_PATH
)

if image is None:
    print("ERROR: Test image could not be loaded.")
    exit()


gray = cv2.cvtColor(
    image,
    cv2.COLOR_BGR2GRAY
)


# --------------------------------------------------
# DETECT FACE
# --------------------------------------------------

detected_faces = face_cascade.detectMultiScale(
    gray,
    scaleFactor=1.2,
    minNeighbors=5,
    minSize=(80, 80)
)


if len(detected_faces) == 0:
    print("No face detected in test image.")
    exit()


# --------------------------------------------------
# RECOGNIZE FACE
# --------------------------------------------------

for (x, y, width, height) in detected_faces:

    face = gray[
        y:y + height,
        x:x + width
    ]

    label, confidence = recognizer.predict(
        face
    )

    print("--------------------------------------")
    print("Face detected")
    print("Predicted label:", label)
    print("LBPH distance:", confidence)
    print(
        "Recognition threshold:",
        RECOGNITION_THRESHOLD
    )

    # --------------------------------------------------
    # DECISION
    # --------------------------------------------------

    if (
        label == 1
        and confidence < RECOGNITION_THRESHOLD
    ):

        print(
            "RESULT: STUDENT RECOGNIZED"
        )

        print(
            "Student ID: 20241ise0219"
        )

    else:

        print(
            "RESULT: UNKNOWN PERSON"
        )

    print("--------------------------------------")