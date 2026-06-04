"""
AI 기반 스마트 횡단보도 위험도 분석 코드 (데이터 파이프라인 연결 버전)

변경 사항 (원본 대비)
- db_pipeline.RiskLogPipeline 추가: 위험도 로그를 PostgreSQL에 저장
- is_person_moving 변수명 혼용 버그 수정 → 차량 분기에서 is_moving_obj 사용
- calculate_person_vehicle_risk 불필요한 반환값(distance) 명시적으로 무시
- detect_video 인자에 save_log 옵션 추가 (기본 True)
"""

import cv2
import time

from config.settings import (
    object_model,
    TARGET_CLASSES,
    PERSON_COLOR,
    VEHICLE_COLOR,
    DANGER_COLOR,
    ROI_COLOR,
    VEHICLE_ROI_COLOR,
)
from src.utils.drawing import draw_korean_text, draw_polygon_roi
from src.analyzer.roi_analyzer import select_roi_polygon
from src.utils.geometry import (
    is_point_in_polygon,
    calculate_vehicle_proximity,
    calculate_person_vehicle_risk,
    get_vehicle_front_point,
)
from src.analyzer.risk_scorer import RiskMonitorWindow
from src.database.pipeline import RiskLogPipeline


def detect_video(video_path: str, save_log: bool = True):
    # --------------------------------------------------
    # 1. ROI 선택
    # --------------------------------------------------
    crosswalk_roi = select_roi_polygon(video_path, "Select Crosswalk ROI", "횡단보도")
    if crosswalk_roi is None:
        print("횡단보도 ROI가 지정되지 않았습니다.")
        return

    vehicle_roi_1 = select_roi_polygon(video_path, "Select Vehicle ROI 1", "차량영역 1")
    if vehicle_roi_1 is None:
        print("차량 ROI 1이 지정되지 않았습니다.")
        return

    vehicle_roi_2 = select_roi_polygon(video_path, "Select Vehicle ROI 2", "차량영역 2")
    if vehicle_roi_2 is None:
        print("차량 ROI 2가 지정되지 않았습니다.")
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("영상을 열 수 없습니다.")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_delay_ms = int(1000 / fps)

    # --------------------------------------------------
    # 2. 추적 상태 변수 초기화
    # --------------------------------------------------
    frame_index = 0
    last_seen = {}
    prev_proximity_by_id = {}
    prev_vehicle_center_by_id = {}
    prev_person_center_by_id = {}
    stop_count_by_id = {}
    current_risk_score = 0

    risk_monitor = RiskMonitorWindow()

    # --------------------------------------------------
    # 3. 데이터 파이프라인 초기화  ← 추가
    # --------------------------------------------------
    pipeline: RiskLogPipeline | None = None
    if save_log:
        pipeline = RiskLogPipeline(video_source=video_path)
        try:
            pipeline.connect()
        except Exception as e:
            print(f"[경고] DB 연결 실패, 로그 저장 없이 실행합니다: {e}")
            pipeline = None

    # --------------------------------------------------
    # 4. 메인 루프
    # --------------------------------------------------
    try:
        while True:
            frame_start_time = time.time()

            ret, frame = cap.read()
            if not ret:
                break

            frame_index += 1
            frame_h, frame_w = frame.shape[:2]

            object_results = object_model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0, 2, 3, 5, 7],
                conf=0.18,
                iou=0.45,
                imgsz=768,
                max_det=300,
                agnostic_nms=True,
                verbose=False,
            )[0]

            # 프레임별 상태 초기화
            person_in_crosswalk = False
            vehicle_in_crosswalk = False
            vehicle_in_vehicle_roi = False
            person_in_vehicle_roi = False

            moving_person_in_crosswalk = False
            moving_person_in_vehicle_roi = False
            moving_vehicle_in_vehicle_roi = False

            detected_objects = []
            detected_persons = []

            frame = draw_polygon_roi(frame, crosswalk_roi, ROI_COLOR, "CROSSWALK ROI")
            frame = draw_polygon_roi(frame, vehicle_roi_1, VEHICLE_ROI_COLOR, "VEHICLE ROI 1")
            frame = draw_polygon_roi(frame, vehicle_roi_2, VEHICLE_ROI_COLOR, "VEHICLE ROI 2")

            for box in object_results.boxes:
                if box.id is None:
                    continue

                cls_id = int(box.cls[0])
                if cls_id not in TARGET_CLASSES:
                    continue

                track_id = int(box.id[0])
                class_name = TARGET_CLASSES[cls_id]

                bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                bbox_center = ((bx1 + bx2) // 2, (by1 + by2) // 2)

                if class_name == "person":
                    point_x = (bx1 + bx2) // 2
                    point_y = by2
                    object_color = PERSON_COLOR

                    prev_person_center = prev_person_center_by_id.get(track_id, bbox_center)
                    person_move_distance = (
                        ((bbox_center[0] - prev_person_center[0]) ** 2)
                        + ((bbox_center[1] - prev_person_center[1]) ** 2)
                    ) ** 0.5
                    is_moving_obj = person_move_distance > 3
                    prev_person_center_by_id[track_id] = bbox_center

                else:
                    # ✅ 버그 수정: 차량 분기에서 is_person_moving을 덮어쓰던 문제 제거
                    #   원본: is_person_moving = False
                    #   수정: is_moving_obj = False (차량이므로 이동 여부는 아래에서 별도 계산)
                    prev_center = prev_vehicle_center_by_id.get(track_id)
                    point_x, point_y = get_vehicle_front_point(
                        bx1, by1, bx2, by2, bbox_center, prev_center,
                    )
                    object_color = VEHICLE_COLOR
                    is_moving_obj = False  # 차량 이동 여부는 11-22에서 계산

                object_point = (point_x, point_y)
                last_seen[track_id] = frame_index

                in_crosswalk_roi = is_point_in_polygon(object_point, crosswalk_roi)
                in_vehicle_roi = (
                    is_point_in_polygon(object_point, vehicle_roi_1)
                    or is_point_in_polygon(object_point, vehicle_roi_2)
                )

                obj_data = {
                    "class_name": class_name,
                    "track_id": track_id,
                    "box": (bx1, by1, bx2, by2),
                    "center": object_point,
                    "bbox_center": bbox_center,
                    "conf": conf,
                    "color": object_color,
                    "is_person_moving": is_moving_obj,  # 사람일 때만 의미 있음
                    "in_crosswalk_roi": in_crosswalk_roi,
                    "in_vehicle_roi": in_vehicle_roi,
                }

                detected_objects.append(obj_data)

                if class_name == "person":
                    detected_persons.append(obj_data)

                    if in_crosswalk_roi:
                        person_in_crosswalk = True
                        if is_moving_obj:
                            moving_person_in_crosswalk = True

                    if in_vehicle_roi:
                        person_in_vehicle_roi = True
                        if is_moving_obj:
                            moving_person_in_vehicle_roi = True

                if class_name == "vehicle":
                    if in_crosswalk_roi:
                        vehicle_in_crosswalk = True
                    if in_vehicle_roi:
                        vehicle_in_vehicle_roi = True

            person_count = len(detected_persons)
            moving_person_exists = any(p["is_person_moving"] for p in detected_persons)

            target_risk_score = 0
            increase_reasons = []
            decrease_reasons = []
            risk_detail = ""

            if person_count == 0:
                decrease_reasons.append("사람이 감지되지 않아 위험도 감소 요인 발생")

            if person_count >= 5:
                target_risk_score = max(target_risk_score, 10)
                increase_reasons.append("보행자 수가 많아 위험도가 소폭 증가함")
            elif person_count >= 3:
                target_risk_score = max(target_risk_score, 5)
                increase_reasons.append("보행자가 여러 명 감지되어 위험도가 소폭 증가함")

            for obj in detected_objects:
                class_name = obj["class_name"]
                track_id = obj["track_id"]
                bx1, by1, bx2, by2 = obj["box"]
                point_x, point_y = obj["center"]
                bbox_center = obj["bbox_center"]
                conf = obj["conf"]
                object_color = obj["color"]
                obj_in_crosswalk_roi = obj["in_crosswalk_roi"]
                obj_in_vehicle_roi = obj["in_vehicle_roi"]

                cv2.rectangle(frame, (bx1, by1), (bx2, by2), object_color, 2)
                cv2.circle(frame, (point_x, point_y), 5, object_color, -1)
                cv2.putText(
                    frame,
                    f"{class_name} ID:{track_id} {conf:.2f}",
                    (bx1, by1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    object_color,
                    2,
                )

                if class_name == "vehicle":
                    proximity = calculate_vehicle_proximity(
                        (bx1, by1, bx2, by2), crosswalk_roi, frame_w, frame_h,
                    )

                    prev_proximity = prev_proximity_by_id.get(track_id, proximity)
                    is_approaching_crosswalk = proximity > prev_proximity + 1
                    prev_proximity_by_id[track_id] = proximity

                    prev_center = prev_vehicle_center_by_id.get(track_id, bbox_center)
                    move_distance = (
                        ((bbox_center[0] - prev_center[0]) ** 2)
                        + ((bbox_center[1] - prev_center[1]) ** 2)
                    ) ** 0.5
                    is_vehicle_moving = move_distance > 3
                    prev_vehicle_center_by_id[track_id] = bbox_center

                    if is_vehicle_moving:
                        stop_count_by_id[track_id] = 0
                    else:
                        stop_count_by_id[track_id] = stop_count_by_id.get(track_id, 0) + 1

                    stop_count = stop_count_by_id.get(track_id, 0)

                    is_vehicle_moving_in_vehicle_roi = obj_in_vehicle_roi and is_vehicle_moving
                    is_vehicle_stopped_in_vehicle_roi = obj_in_vehicle_roi and not is_vehicle_moving

                    if is_vehicle_moving_in_vehicle_roi:
                        moving_vehicle_in_vehicle_roi = True

                    normal_text = f"crosswalk proximity: {proximity}"
                    if person_in_crosswalk or person_in_vehicle_roi:
                        danger_text = f"person danger proximity: {proximity}"
                        proximity_color = DANGER_COLOR
                    else:
                        danger_text = "person danger proximity: -"
                        proximity_color = VEHICLE_COLOR

                    cv2.putText(frame, normal_text, (bx1, by2 + 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, VEHICLE_COLOR, 2)
                    cv2.putText(frame, danger_text, (bx1, by2 + 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, proximity_color, 2)

                    max_person_vehicle_risk = 0
                    for person_obj in detected_persons:
                        # ✅ 수정: distance는 사용하지 않으므로 _ 로 무시
                        person_vehicle_risk, _ = calculate_person_vehicle_risk(
                            (point_x, point_y), person_obj["center"], frame_w, frame_h,
                        )
                        if person_vehicle_risk > max_person_vehicle_risk:
                            max_person_vehicle_risk = person_vehicle_risk

                    # 위험도 조건 분기 (원본과 동일한 순서/수치 유지)
                    if (
                        (moving_person_in_crosswalk or moving_person_in_vehicle_roi)
                        and is_vehicle_moving_in_vehicle_roi
                    ):
                        danger_score = max(80, proximity, max_person_vehicle_risk)
                        target_risk_score = max(target_risk_score, danger_score)
                        increase_reasons.append("사람이 횡단보도/차량영역에서 움직이고 차량도 차량영역 안에서 움직임")

                    elif person_in_crosswalk and proximity >= 100:
                        target_risk_score = max(target_risk_score, 100)
                        increase_reasons.append("횡단보도에 사람이 있고 차량이 횡단보도에 진입함")

                    elif person_in_crosswalk and is_vehicle_moving:
                        danger_score = max(60, proximity, max_person_vehicle_risk)
                        target_risk_score = max(target_risk_score, danger_score)
                        increase_reasons.append("횡단보도에 사람이 있는데 차량이 움직임")

                    elif person_in_vehicle_roi and is_vehicle_moving:
                        danger_score = max(70, proximity, max_person_vehicle_risk)
                        target_risk_score = max(target_risk_score, danger_score)
                        increase_reasons.append("차량영역에 사람이 있는데 차량이 움직임")

                    elif moving_person_exists and is_vehicle_moving:
                        danger_score = max(50, max_person_vehicle_risk)
                        target_risk_score = max(target_risk_score, danger_score)
                        increase_reasons.append("사람이 움직이고 차량도 움직임")

                    elif person_in_crosswalk and is_approaching_crosswalk:
                        target_risk_score = max(target_risk_score, proximity)
                        increase_reasons.append("횡단보도에 사람이 있고 차량이 횡단보도에 가까워짐")

                    elif person_in_vehicle_roi and is_approaching_crosswalk:
                        target_risk_score = max(target_risk_score, proximity)
                        increase_reasons.append("차량영역에 사람이 있고 차량이 횡단보도에 가까워짐")

                    elif max_person_vehicle_risk >= 60 and is_vehicle_moving:
                        target_risk_score = max(target_risk_score, max_person_vehicle_risk)
                        increase_reasons.append("차량과 사람이 가까운 상태에서 차량이 움직임")

                    elif person_in_crosswalk and not is_vehicle_moving:
                        if stop_count < 45:
                            target_risk_score = max(target_risk_score, max(50, proximity))
                            decrease_reasons.append("차량이 일시정지 중이라 위험도 감소 요인 발생")
                        else:
                            target_risk_score = max(target_risk_score, 30)
                            decrease_reasons.append("차량이 장시간 정지하여 위험도가 감소함")

                    elif person_in_vehicle_roi and is_vehicle_stopped_in_vehicle_roi:
                        if stop_count < 45:
                            target_risk_score = max(target_risk_score, 50)
                            decrease_reasons.append("차량영역 안 차량이 일시정지 중이라 위험도 감소 요인 발생")
                        else:
                            target_risk_score = max(target_risk_score, 25)
                            decrease_reasons.append("차량영역 안 차량이 장시간 정지하여 위험도가 감소함")

                    elif is_vehicle_stopped_in_vehicle_roi:
                        if stop_count < 45:
                            target_risk_score = max(target_risk_score, 15)
                            decrease_reasons.append("차량영역 안 차량이 정지 중이라 위험도 감소 요인 발생")
                        else:
                            target_risk_score = max(target_risk_score, 5)
                            decrease_reasons.append("차량영역 안 차량이 장시간 정지하여 위험도가 감소함")

                    elif not person_in_crosswalk and not person_in_vehicle_roi and is_vehicle_moving:
                        if obj_in_crosswalk_roi:
                            target_risk_score = max(target_risk_score, 20)
                            increase_reasons.append("사람은 없지만 차량이 횡단보도 영역에서 움직임")
                        elif obj_in_vehicle_roi:
                            target_risk_score = max(target_risk_score, 10)
                            increase_reasons.append("사람은 없지만 차량이 차량영역에서 움직임")

                    risk_detail = (
                        f"사람수:{person_count}, "
                        f"횡단보도사람:{person_in_crosswalk}, "
                        f"차량영역사람:{person_in_vehicle_roi}, "
                        f"차량이동:{is_vehicle_moving}, "
                        f"차량영역차량:{obj_in_vehicle_roi}, "
                        f"접근도:{proximity}, "
                        f"정지시간:{stop_count}"
                    )

            target_risk_score = min(100, target_risk_score)

            if current_risk_score < target_risk_score:
                current_risk_score += 4
                current_risk_score = min(current_risk_score, target_risk_score)
            elif current_risk_score > target_risk_score:
                current_risk_score -= 2
                current_risk_score = max(current_risk_score, target_risk_score)

            frame = draw_korean_text(
                frame,
                f"위험도: {int(current_risk_score)} / 100",
                (20, frame_h // 2),
                34,
                DANGER_COLOR if current_risk_score >= 60 else (0, 255, 255),
            )

            risk_monitor.update(current_risk_score, increase_reasons, decrease_reasons, risk_detail)
            risk_monitor.show()

            # ✅ 추가: 매 프레임 파이프라인에 push
            if pipeline is not None:
                pipeline.push(
                    frame_index=frame_index,
                    fps=fps,
                    risk_score=current_risk_score,
                    increase_reasons=increase_reasons,
                    decrease_reasons=decrease_reasons,
                    detail=risk_detail,
                )

            # 오래된 track_id 정리
            remove_ids = [
                tid for tid, seen in last_seen.items()
                if frame_index - seen > 60
            ]
            for tid in remove_ids:
                last_seen.pop(tid, None)
                prev_proximity_by_id.pop(tid, None)
                prev_vehicle_center_by_id.pop(tid, None)
                prev_person_center_by_id.pop(tid, None)
                stop_count_by_id.pop(tid, None)

            cv2.imshow("YOLO11s ROI Detection", frame)

            elapsed_ms = int((time.time() - frame_start_time) * 1000)
            delay = max(1, frame_delay_ms - elapsed_ms)
            if cv2.waitKey(delay) & 0xFF == ord("q"):
                break

    finally:
        # ✅ 추가: 정상 종료 / 강제 종료(q) 모두 flush_and_close 보장
        if pipeline is not None:
            pipeline.flush_and_close()

        cap.release()
        cv2.destroyAllWindows()