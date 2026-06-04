"""
YOLOv8 탐지 모듈 단위 테스트

실제 모델 파일(.pt) 없이도 실행 가능하도록
핵심 유틸리티 함수 위주로 테스트한다.
"""

import pytest
import numpy as np
import cv2


# ──────────────────────────────────────────
# geometry_utils 함수 테스트 (탐지와 직접 연관)
# ──────────────────────────────────────────

from src.utils.geometry import (
    is_point_in_polygon,
    calculate_vehicle_proximity,
    calculate_person_vehicle_risk,
    get_vehicle_front_point,
)


class TestIsPointInPolygon:
    """ROI 내부/외부 판별 테스트."""

    def setup_method(self):
        # 100x100 사각형 ROI
        self.polygon = np.array([
            [0, 0], [100, 0], [100, 100], [0, 100]
        ], dtype=np.int32)

    def test_point_inside_polygon(self):
        """ROI 내부 점은 True를 반환해야 한다."""
        assert is_point_in_polygon((50, 50), self.polygon) is True

    def test_point_outside_polygon(self):
        """ROI 외부 점은 False를 반환해야 한다."""
        assert is_point_in_polygon((200, 200), self.polygon) is False

    def test_point_on_boundary(self):
        """경계선 위의 점은 True를 반환해야 한다."""
        assert is_point_in_polygon((0, 0), self.polygon) is True

    def test_triangle_roi(self):
        """삼각형 ROI에서도 올바르게 동작해야 한다."""
        triangle = np.array([[50, 0], [100, 100], [0, 100]], dtype=np.int32)
        assert is_point_in_polygon((50, 80), triangle) is True
        assert is_point_in_polygon((10, 10), triangle) is False


class TestCalculateVehicleProximity:
    """차량-횡단보도 접근도(proximity) 계산 테스트."""

    def setup_method(self):
        self.frame_w = 1280
        self.frame_h = 720
        # 화면 중앙에 횡단보도 ROI
        self.crosswalk_roi = np.array([
            [500, 300], [780, 300], [780, 420], [500, 420]
        ], dtype=np.int32)

    def test_vehicle_inside_crosswalk_returns_100(self):
        """횡단보도 내부의 차량은 proximity 100을 반환해야 한다."""
        # 차량 bbox가 횡단보도 내부
        vehicle_box = (520, 310, 600, 380)
        proximity = calculate_vehicle_proximity(
            vehicle_box, self.crosswalk_roi, self.frame_w, self.frame_h
        )
        assert proximity == 100

    def test_vehicle_far_from_crosswalk_returns_low_proximity(self):
        """횡단보도에서 먼 차량은 낮은 proximity를 반환해야 한다."""
        # 화면 왼쪽 끝에 차량
        vehicle_box = (0, 0, 80, 60)
        proximity = calculate_vehicle_proximity(
            vehicle_box, self.crosswalk_roi, self.frame_w, self.frame_h
        )
        assert proximity < 50

    def test_proximity_range_is_0_to_100(self):
        """proximity 값은 항상 0~100 범위여야 한다."""
        vehicle_box = (1200, 600, 1280, 720)
        proximity = calculate_vehicle_proximity(
            vehicle_box, self.crosswalk_roi, self.frame_w, self.frame_h
        )
        assert 0 <= proximity <= 100


class TestCalculatePersonVehicleRisk:
    """차량-보행자 위험도 계산 테스트."""

    def setup_method(self):
        self.frame_w = 1280
        self.frame_h = 720

    def test_same_position_returns_100(self):
        """차량과 보행자가 같은 위치면 위험도 100이어야 한다."""
        point = (640, 360)
        risk, distance = calculate_person_vehicle_risk(point, point, self.frame_w, self.frame_h)
        assert risk == 100
        assert distance == 0.0

    def test_far_apart_returns_low_risk(self):
        """멀리 떨어진 경우 낮은 위험도를 반환해야 한다."""
        vehicle_point = (0, 0)
        person_point = (1280, 720)
        risk, distance = calculate_person_vehicle_risk(
            vehicle_point, person_point, self.frame_w, self.frame_h
        )
        assert risk == 0

    def test_risk_range_is_0_to_100(self):
        """위험도 값은 항상 0~100 범위여야 한다."""
        risk, _ = calculate_person_vehicle_risk(
            (100, 100), (200, 200), self.frame_w, self.frame_h
        )
        assert 0 <= risk <= 100

    def test_returns_tuple_of_risk_and_distance(self):
        """반환값은 (risk, distance) 튜플이어야 한다."""
        result = calculate_person_vehicle_risk(
            (100, 100), (150, 150), self.frame_w, self.frame_h
        )
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestGetVehicleFrontPoint:
    """차량 진행 방향 앞 꼭짓점 계산 테스트."""

    def test_moving_right_returns_right_edge(self):
        """오른쪽으로 이동 중이면 오른쪽 엣지를 반환해야 한다."""
        current_center = (110, 50)
        prev_center = (100, 50)
        x, y = get_vehicle_front_point(80, 30, 140, 70, current_center, prev_center)
        assert x == 140  # bx2 (오른쪽 엣지)

    def test_moving_down_returns_bottom_edge(self):
        """아래로 이동 중이면 아래쪽 엣지를 반환해야 한다."""
        current_center = (100, 110)
        prev_center = (100, 100)
        x, y = get_vehicle_front_point(80, 90, 120, 130, current_center, prev_center)
        assert y == 130  # by2 (아래쪽 엣지)

    def test_no_prev_center_returns_left_edge(self):
        """이전 위치가 없으면 기본값(왼쪽 엣지)을 반환해야 한다."""
        x, y = get_vehicle_front_point(80, 30, 140, 70, (110, 50), None)
        assert x == 80  # bx1 (기본값)