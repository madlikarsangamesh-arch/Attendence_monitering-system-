import cv2
import os
import numpy as np


# --------------------------------------------------
# PROJECT PATH
# --------------------------------------------------

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATASET_PATH = os.path.join(
    BASE_DIR,
    "dataset"
)

MODEL_PATH = os.path.join(
    BASE_DIR,
    "backend",
    "trainer.yml"
)


# --------------------------------------------------
# FACE DETECTOR
# --------------------------------------------------

cascade_path = os.path.join(
    BASE_DIR,
    "backend",
    "haarcascade_frontalface_default.xml"
)

print("Cascade path:")
print(cascade_path)
print()

FACE_CASCADE = cv2.CascadeClassifier(
    cascade_path
)

if FACE_CASCADE.empty():
    print("ERROR: Face detector could not be loaded.")
    exit()


# --------------------------------------------------
# LBPH FACE RECOGNIZER
# --------------------------------------------------

recognizer = cv2.face.LBPHFaceRecognizer_create()

faces = []
labels = []


# --------------------------------------------------
# READ DATASET
# --------------------------------------------------

print("Dataset path:")
print(DATASET_PATH)
print()

print("Starting face training...")
print()


for filename in os.listdir(DATASET_PATH):

    # --------------------------------------------------
    # ONLY PROCESS IMAGE FILES
    # --------------------------------------------------

    if not filename.lower().endswith(
        (".jpg", ".jpeg", ".png")
    ):
        continue


    # --------------------------------------------------
    # IGNORE SIR'S PHOTO
    # --------------------------------------------------

    if filename.lower() == "sir.jpg":

        print(
            f"Skipping unknown person image: {filename}"
        )

        continue


    # --------------------------------------------------
    # IMAGE PATH
    # --------------------------------------------------

    image_path = os.path.join(
        DATASET_PATH,
        filename
    )


    # --------------------------------------------------
    # READ IMAGE
    # --------------------------------------------------

    image = cv2.imread(
        image_path,
        cv2.IMREAD_GRAYSCALE
    )

    if image is None:

        print(
            f"Could not read: {filename}"
        )

        continue


    # --------------------------------------------------
    # DETECT FACE
    # --------------------------------------------------

    detected_faces = FACE_CASCADE.detectMultiScale(
        image,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(80, 80)
    )


    # --------------------------------------------------
    # NO FACE FOUND
    # --------------------------------------------------

    if len(detected_faces) == 0:

        print(
            f"No face detected: {filename}"
        )

        continue


    # --------------------------------------------------
    # SAVE FACE SAMPLES
    # --------------------------------------------------

    for (x, y, width, height) in detected_faces:

        face = image[
            y:y + height,
            x:x + width
        ]


        # Add face image
        faces.append(face)


        # Label 1 = Sangamesh
        # Student ID = 20241ise0219
        labels.append(1)


        print(
            f"Face detected: {filename}"
        )


# --------------------------------------------------
# CHECK TRAINING DATA
# --------------------------------------------------

if len(faces) == 0:

    print()
    print("ERROR: No faces were detected.")
    print("Check your photos.")
    exit()


# --------------------------------------------------
# TRAIN MODEL
# --------------------------------------------------

print()

print(
    f"Training with {len(faces)} face samples..."
)

# Convert labels into NumPy array
labels_array = np.array(
    labels,
    dtype=np.int32
)

recognizer.train(
    faces,
    labels_array
)


# --------------------------------------------------
# SAVE MODEL
# --------------------------------------------------

recognizer.write(
    MODEL_PATH
)


# --------------------------------------------------
# SUCCESS MESSAGE
# --------------------------------------------------

print()

print("======================================")
print("FACE TRAINING COMPLETED")
print("======================================")

print(
    f"Training samples: {len(faces)}"
)

print(
    "Student ID: 20241ise0219"
)

print(
    f"Model saved at: {MODEL_PATH}"
)