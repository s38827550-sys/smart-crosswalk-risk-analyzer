import cv2
import numpy as np


def select_roi_polygon(video_path, window_name, label):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print("영상을 열 수 없습니다.")
        return None

    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("첫 프레임을 읽을 수 없습니다.")
        return None

    points = []
    clone = frame.copy()

    def mouse_callback(event, x, y, flags, param):
        nonlocal clone

        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))
            clone = frame.copy()

            for point in points:
                cv2.circle(clone, point, 5, (0, 0, 255), -1)

            if len(points) >= 2:
                cv2.polylines(
                    clone,
                    [np.array(points, dtype=np.int32)],
                    False,
                    (0, 255, 255),
                    2,
                )

            cv2.imshow(window_name, clone)

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)

    print(f"{label} ROI를 마우스로 찍으세요.")
    print("왼쪽 클릭: 점 추가")
    print("Enter: ROI 확정")
    print("R: 다시 그리기")
    print("ESC: 종료")

    while True:
        cv2.imshow(window_name, clone)
        key = cv2.waitKey(1) & 0xFF

        if key == 13:
            if len(points) >= 3:
                break
            else:
                print("ROI는 최소 3개 점이 필요합니다.")

        elif key == ord("r"):
            points = []
            clone = frame.copy()

        elif key == 27:
            cv2.destroyWindow(window_name)
            return None

    cv2.destroyWindow(window_name)
    return np.array(points, dtype=np.int32)