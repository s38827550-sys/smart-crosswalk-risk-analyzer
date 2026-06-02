from ultralytics import YOLO

object_model = YOLO("yolo11s.pt")

TARGET_CLASSES = {
    0: "person",
    2: "vehicle",
    3: "vehicle",
    5: "vehicle",
    7: "vehicle",
}

PERSON_COLOR = (255, 255, 80)
VEHICLE_COLOR = (80, 255, 80)
DANGER_COLOR = (0, 0, 255)
ROI_COLOR = (255, 0, 0)
VEHICLE_ROI_COLOR = (0, 255, 255)