"""
데이터베이스 파이프라인 단위 테스트

실제 DB 연결 없이 파이프라인 내부 로직을 테스트한다.
DB 연결이 필요한 테스트는 pytest.mark.integration으로 분리한다.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from src.database.pipeline import RiskLogPipeline, RISK_LOG_DELTA, RISK_EVENT_THRESHOLD


class TestRiskLogPipelineInit:
    """파이프라인 초기화 테스트."""

    def test_session_id_contains_video_name(self):
        """세션 ID에 영상 파일명이 포함되어야 한다."""
        pipeline = RiskLogPipeline(video_source="test_video.mp4")
        assert "test_video" in pipeline.session_id

    def test_session_id_is_unique(self):
        """같은 파일로 생성한 두 파이프라인의 세션 ID는 달라야 한다."""
        import time
        p1 = RiskLogPipeline(video_source="video.mp4")
        time.sleep(1.1)  # 타임스탬프 차이 확보
        p2 = RiskLogPipeline(video_source="video.mp4")
        assert p1.session_id != p2.session_id

    def test_initial_state_is_none(self):
        """초기 DB 연결 상태는 None이어야 한다."""
        pipeline = RiskLogPipeline(video_source="test.mp4")
        assert pipeline._conn is None

    def test_push_without_connect_does_nothing(self):
        """connect() 전 push()는 아무것도 하지 않아야 한다 (에러 없이)."""
        pipeline = RiskLogPipeline(video_source="test.mp4")
        # 에러 없이 실행되어야 함
        pipeline.push(
            frame_index=1, fps=30.0, risk_score=50,
            increase_reasons=[], decrease_reasons=[], detail=""
        )


class TestRiskLogDelta:
    """risk_log 저장 조건 (변화량 임계값) 테스트."""

    def setup_method(self):
        self.pipeline = RiskLogPipeline(video_source="test.mp4")
        # DB 커서 목업
        self.pipeline._conn = MagicMock()
        self.pipeline._cursor = MagicMock()
        self.pipeline._cursor.fetchone.return_value = (1,)

    def test_first_frame_always_logged(self):
        """첫 프레임은 무조건 risk_log에 저장되어야 한다."""
        self.pipeline._handle_risk_log(
            frame_index=1, elapsed_sec=0.0, risk_int=50,
            increase_reasons=[], decrease_reasons=[], detail="", now=None
        )
        assert self.pipeline._cursor.execute.called

    def test_small_delta_not_logged(self):
        """변화량이 RISK_LOG_DELTA 미만이면 저장하지 않아야 한다."""
        self.pipeline._last_logged_risk = 50

        self.pipeline._cursor.reset_mock()
        self.pipeline._handle_risk_log(
            frame_index=2, elapsed_sec=0.1,
            risk_int=50 + RISK_LOG_DELTA - 1,  # 임계값 미만
            increase_reasons=[], decrease_reasons=[], detail="", now=None
        )
        assert not self.pipeline._cursor.execute.called

    def test_large_delta_is_logged(self):
        """변화량이 RISK_LOG_DELTA 이상이면 저장해야 한다."""
        self.pipeline._last_logged_risk = 50

        self.pipeline._cursor.reset_mock()
        self.pipeline._handle_risk_log(
            frame_index=2, elapsed_sec=0.1,
            risk_int=50 + RISK_LOG_DELTA,  # 임계값 도달
            increase_reasons=[], decrease_reasons=[], detail="", now=None
        )
        assert self.pipeline._cursor.execute.called


class TestRiskEventTracking:
    """위험 이벤트 구간 추적 테스트."""

    def setup_method(self):
        import datetime
        self.pipeline = RiskLogPipeline(video_source="test.mp4")
        self.pipeline._conn = MagicMock()
        self.pipeline._cursor = MagicMock()
        self.pipeline._cursor.fetchone.return_value = (1,)
        self.now = datetime.datetime.now()

    def test_event_starts_when_risk_exceeds_threshold(self):
        """위험도가 임계값 이상이면 이벤트가 시작되어야 한다."""
        assert self.pipeline._event_active is False

        self.pipeline._handle_risk_event(
            frame_index=1,
            elapsed_sec=0.0,
            risk_int=RISK_EVENT_THRESHOLD,
            increase_reasons=["차량 접근"],
            now=self.now,
        )
        assert self.pipeline._event_active is True

    def test_event_ends_when_risk_drops_below_threshold(self):
        """위험도가 임계값 아래로 떨어지면 이벤트가 종료되어야 한다."""
        # 이벤트 시작 상태로 설정
        self.pipeline._event_active = True
        self.pipeline._event_id = 1
        self.pipeline._event_start_frame = 1
        self.pipeline._event_start_elapsed = 0.0
        self.pipeline._event_peak_risk = 80

        self.pipeline._handle_risk_event(
            frame_index=60,
            elapsed_sec=2.0,
            risk_int=RISK_EVENT_THRESHOLD - 1,  # 임계값 아래
            increase_reasons=[],
            now=self.now,
        )
        assert self.pipeline._event_active is False
        assert self.pipeline._event_id is None

    def test_peak_risk_updates_during_event(self):
        """이벤트 진행 중 더 높은 위험도가 오면 peak_risk가 갱신되어야 한다."""
        self.pipeline._event_active = True
        self.pipeline._event_id = 1
        self.pipeline._event_peak_risk = 70

        self.pipeline._handle_risk_event(
            frame_index=30,
            elapsed_sec=1.0,
            risk_int=95,  # 더 높은 위험도
            increase_reasons=[],
            now=self.now,
        )
        assert self.pipeline._event_peak_risk == 95


class TestMostCommon:
    """빈도 기반 최다 등장 요인 추출 테스트."""

    def test_returns_most_frequent_item(self):
        """가장 많이 등장한 항목을 반환해야 한다."""
        lst = ["A", "B", "A", "C", "A"]
        result = RiskLogPipeline._most_common(lst)
        assert result == "A"

    def test_empty_list_returns_none(self):
        """빈 리스트는 None을 반환해야 한다."""
        result = RiskLogPipeline._most_common([])
        assert result is None

    def test_single_item(self):
        """단일 항목 리스트는 그 항목을 반환해야 한다."""
        result = RiskLogPipeline._most_common(["only"])
        assert result == "only"