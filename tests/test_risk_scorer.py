"""
위험도 분석 모듈 단위 테스트

RiskMonitorWindow의 상태 관리 로직과
ObjectTracker의 이동/정지 판별 로직을 테스트한다.
"""

import pytest
from src.tracker.object_tracker import ObjectTracker, TrackState


class TestTrackState:
    """단일 트래킹 객체 상태 테스트."""

    def test_first_update_returns_not_moving(self):
        """첫 업데이트는 이전 위치가 없으므로 False를 반환해야 한다."""
        state = TrackState(track_id=1)
        is_moving = state.update(center=(100, 100), frame_index=1)
        assert is_moving is False

    def test_large_movement_returns_moving(self):
        """임계값 초과 이동은 True를 반환해야 한다."""
        state = TrackState(track_id=1)
        state.update(center=(100, 100), frame_index=1)
        is_moving = state.update(center=(150, 150), frame_index=2)
        assert is_moving is True

    def test_small_movement_returns_not_moving(self):
        """임계값 이하 이동은 False를 반환해야 한다."""
        state = TrackState(track_id=1)
        state.update(center=(100, 100), frame_index=1)
        is_moving = state.update(center=(101, 101), frame_index=2, move_threshold=3.0)
        assert is_moving is False

    def test_stop_count_increases_when_not_moving(self):
        """정지 시 stop_count가 증가해야 한다."""
        state = TrackState(track_id=1)
        state.update(center=(100, 100), frame_index=1)
        state.update(center=(100, 100), frame_index=2)
        state.update(center=(100, 100), frame_index=3)
        assert state.stop_count == 2

    def test_stop_count_resets_when_moving(self):
        """이동 재개 시 stop_count가 0으로 초기화되어야 한다."""
        state = TrackState(track_id=1)
        state.update(center=(100, 100), frame_index=1)
        state.update(center=(100, 100), frame_index=2)  # 정지
        state.update(center=(100, 100), frame_index=3)  # 정지
        assert state.stop_count == 2

        state.update(center=(200, 200), frame_index=4)  # 이동
        assert state.stop_count == 0

    def test_trajectory_stores_recent_centers(self):
        """궤적은 최근 30개 중심점만 유지해야 한다."""
        state = TrackState(track_id=1)
        for i in range(40):
            state.update(center=(i * 5, i * 5), frame_index=i)
        assert len(state.trajectory) == 30

    def test_get_move_distance_no_prev(self):
        """이전 위치 없을 때 거리는 0이어야 한다."""
        state = TrackState(track_id=1)
        assert state.get_move_distance((100, 100)) == 0.0

    def test_get_move_distance_with_prev(self):
        """이전 위치 있을 때 유클리드 거리를 반환해야 한다."""
        state = TrackState(track_id=1)
        state.update(center=(0, 0), frame_index=1)
        distance = state.get_move_distance((3, 4))
        assert distance == pytest.approx(5.0)


class TestObjectTracker:
    """전체 트래킹 세션 관리 테스트."""

    def test_get_or_create_new_track(self):
        """없는 track_id는 새 TrackState를 생성해야 한다."""
        tracker = ObjectTracker()
        state = tracker.get_or_create(track_id=42)
        assert isinstance(state, TrackState)
        assert state.track_id == 42

    def test_get_or_create_existing_track(self):
        """같은 track_id는 동일한 TrackState를 반환해야 한다."""
        tracker = ObjectTracker()
        state1 = tracker.get_or_create(track_id=1)
        state2 = tracker.get_or_create(track_id=1)
        assert state1 is state2

    def test_active_count(self):
        """추적 중인 객체 수가 정확해야 한다."""
        tracker = ObjectTracker()
        tracker.get_or_create(1)
        tracker.get_or_create(2)
        tracker.get_or_create(3)
        assert tracker.active_count == 3

    def test_cleanup_removes_old_tracks(self):
        """오래된 track_id는 cleanup 후 제거되어야 한다."""
        tracker = ObjectTracker()
        state = tracker.get_or_create(track_id=1)
        state.last_seen_frame = 1  # 프레임 1에서 마지막 등장

        tracker.cleanup(current_frame=100, max_age=60)
        assert tracker.active_count == 0

    def test_cleanup_keeps_recent_tracks(self):
        """최근에 등장한 track_id는 cleanup 후에도 유지되어야 한다."""
        tracker = ObjectTracker()
        state = tracker.get_or_create(track_id=1)
        state.last_seen_frame = 90  # 최근 등장

        tracker.cleanup(current_frame=100, max_age=60)
        assert tracker.active_count == 1

    def test_get_stop_count_nonexistent_id(self):
        """없는 track_id의 stop_count는 0이어야 한다."""
        tracker = ObjectTracker()
        assert tracker.get_stop_count(999) == 0

    def test_get_trajectory_nonexistent_id(self):
        """없는 track_id의 trajectory는 빈 리스트여야 한다."""
        tracker = ObjectTracker()
        assert tracker.get_trajectory(999) == []

    def test_multiple_tracks_independent(self):
        """서로 다른 track_id는 독립적으로 상태를 유지해야 한다."""
        tracker = ObjectTracker()

        state1 = tracker.get_or_create(1)
        state1.update((100, 100), 1)
        state1.update((100, 100), 2)  # 정지

        state2 = tracker.get_or_create(2)
        state2.update((200, 200), 1)
        state2.update((300, 300), 2)  # 이동

        assert tracker.get_stop_count(1) == 1
        assert tracker.get_stop_count(2) == 0