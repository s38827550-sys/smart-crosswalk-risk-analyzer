"""
Object Tracker 유틸리티 모듈

YOLOv8의 ByteTrack 기반 트래킹 결과를 후처리하는 유틸리티 클래스.
yolo_service.py에서 model.track(..., tracker="bytetrack.yaml")로 트래킹하고,
이 모듈은 트래킹 상태(이동 감지, 정지 카운트, 궤적 관리)를 관리한다.
"""

from collections import defaultdict
from typing import Optional


class TrackState:
    """단일 트래킹 객체의 상태를 관리하는 클래스."""

    def __init__(self, track_id: int):
        self.track_id = track_id
        self.prev_center: Optional[tuple] = None
        self.stop_count: int = 0
        self.last_seen_frame: int = 0
        self.trajectory: list = []  # 최근 N개 중심점 기록

    def update(self, center: tuple, frame_index: int, move_threshold: float = 3.0) -> bool:
        """
        중심점을 업데이트하고 이동 여부를 반환한다.

        Args:
            center: 현재 프레임의 중심점 (x, y)
            frame_index: 현재 프레임 번호
            move_threshold: 이동으로 판단하는 최소 픽셀 거리

        Returns:
            is_moving: 이동 중이면 True
        """
        self.last_seen_frame = frame_index
        self.trajectory.append(center)

        # 궤적은 최근 30프레임만 유지
        if len(self.trajectory) > 30:
            self.trajectory.pop(0)

        if self.prev_center is None:
            self.prev_center = center
            return False

        move_distance = (
            (center[0] - self.prev_center[0]) ** 2
            + (center[1] - self.prev_center[1]) ** 2
        ) ** 0.5

        is_moving = move_distance > move_threshold

        if is_moving:
            self.stop_count = 0
        else:
            self.stop_count += 1

        self.prev_center = center
        return is_moving

    def get_move_distance(self, center: tuple) -> float:
        """이전 위치와의 거리를 반환한다."""
        if self.prev_center is None:
            return 0.0
        return (
            (center[0] - self.prev_center[0]) ** 2
            + (center[1] - self.prev_center[1]) ** 2
        ) ** 0.5


class ObjectTracker:
    """
    전체 트래킹 세션을 관리하는 클래스.

    ByteTrack은 YOLO 내부에서 동작하며,
    이 클래스는 그 결과(track_id)를 받아 상태를 유지한다.

    Usage:
        tracker = ObjectTracker()

        # 매 프레임 루프 안에서
        state = tracker.get_or_create(track_id)
        is_moving = state.update(center, frame_index)
        tracker.cleanup(frame_index, max_age=60)
    """

    def __init__(self):
        self._states: dict[int, TrackState] = {}

    def get_or_create(self, track_id: int) -> TrackState:
        """track_id에 해당하는 TrackState를 반환한다. 없으면 새로 생성."""
        if track_id not in self._states:
            self._states[track_id] = TrackState(track_id)
        return self._states[track_id]

    def cleanup(self, current_frame: int, max_age: int = 60):
        """
        일정 프레임 이상 등장하지 않은 트래킹 객체를 제거한다.

        Args:
            current_frame: 현재 프레임 번호
            max_age: 이 프레임 수 이상 미등장 시 제거 (기본 60프레임 = 약 2초)
        """
        remove_ids = [
            tid for tid, state in self._states.items()
            if current_frame - state.last_seen_frame > max_age
        ]
        for tid in remove_ids:
            del self._states[tid]

    def get_stop_count(self, track_id: int) -> int:
        """특정 track_id의 정지 프레임 수를 반환한다."""
        state = self._states.get(track_id)
        return state.stop_count if state else 0

    def get_trajectory(self, track_id: int) -> list:
        """특정 track_id의 최근 궤적 좌표 리스트를 반환한다."""
        state = self._states.get(track_id)
        return state.trajectory if state else []

    @property
    def active_count(self) -> int:
        """현재 추적 중인 객체 수를 반환한다."""
        return len(self._states)