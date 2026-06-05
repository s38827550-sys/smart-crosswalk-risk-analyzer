# """
# 위험도 로그 저장 데이터 파이프라인 (PostgreSQL)

# 저장 전략
# - risk_log    : 위험도가 ±10 이상 변화할 때마다 즉시 저장 (주요 변화 포인트)
# - risk_summary: 매 1초마다 해당 초의 평균/최대 위험도 및 감지 요약 저장
# - risk_event  : 위험도 60 이상 구간을 하나의 이벤트로 묶어서 저장

# 사용법
#     pipeline = RiskLogPipeline(video_source="test_video.mp4")
#     pipeline.connect()

#     # yolo_service.py 루프 안에서 매 프레임마다 호출
#     pipeline.push(
#         frame_index=frame_index,
#         fps=fps,
#         risk_score=current_risk_score,
#         increase_reasons=increase_reasons,
#         decrease_reasons=decrease_reasons,
#         detail=risk_detail,
#     )

#     # 영상 종료 후 반드시 호출
#     pipeline.flush_and_close()
# """

import os
import time
import json
import datetime
import psycopg2
import psycopg2.extras
from collections import deque
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ==============================
# PostgreSQL 연결 설정
# 환경변수 또는 직접 수정해서 사용
# ==============================
def _get_db_config() -> dict:
    return {
        "host":     os.getenv("PG_HOST"),
        "port":     int(os.getenv("PG_PORT")),
        "dbname":   os.getenv("PG_DBNAME"),
        "user":     os.getenv("PG_USER"),
        "password": os.getenv("PG_PASSWORD"),
    }

# 위험도 임계값 상수
RISK_EVENT_THRESHOLD  = 60   # 이 점수 이상이면 위험 이벤트 구간으로 기록
RISK_LOG_DELTA        = 10   # 이전 저장 대비 변화량이 이 이상이면 risk_log에 저장
SUMMARY_INTERVAL_SEC  = 1.0  # risk_summary 저장 주기 (초)


# ==============================
# DDL: 테이블 생성 SQL
# ==============================
CREATE_TABLES_SQL = """
-- 위험도 주요 변화 포인트 로그
CREATE TABLE IF NOT EXISTS risk_log (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT        NOT NULL,           -- 영상 파일명 + 시작시각
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    frame_index     INTEGER     NOT NULL,
    elapsed_sec     FLOAT       NOT NULL,           -- 영상 시작 기준 경과 초
    risk_score      SMALLINT    NOT NULL,
    prev_risk_score SMALLINT    NOT NULL,
    delta           SMALLINT    NOT NULL,           -- risk_score - prev_risk_score
    increase_reasons TEXT[],                        -- 위험도 증가 요인 배열
    decrease_reasons TEXT[],                        -- 위험도 감소 요인 배열
    detail          TEXT
);

-- 초 단위 집계 요약
CREATE TABLE IF NOT EXISTS risk_summary (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT        NOT NULL,
    bucket_at       TIMESTAMPTZ NOT NULL,           -- 해당 1초 구간의 시작 시각
    elapsed_sec     FLOAT       NOT NULL,
    avg_risk        FLOAT       NOT NULL,
    max_risk        SMALLINT    NOT NULL,
    min_risk        SMALLINT    NOT NULL,
    frame_count     SMALLINT    NOT NULL,           -- 해당 1초 동안 처리된 프레임 수
    any_increase    BOOLEAN     NOT NULL DEFAULT FALSE,
    any_decrease    BOOLEAN     NOT NULL DEFAULT FALSE,
    top_increase_reason TEXT,                       -- 해당 초 내 가장 많이 등장한 증가 요인
    top_decrease_reason TEXT
);

-- 위험 이벤트 구간 (위험도 60 이상 지속 구간)
CREATE TABLE IF NOT EXISTS risk_event (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,                    -- NULL이면 아직 진행 중
    start_frame     INTEGER     NOT NULL,
    end_frame       INTEGER,
    start_elapsed   FLOAT       NOT NULL,
    end_elapsed     FLOAT,
    peak_risk       SMALLINT    NOT NULL DEFAULT 0, -- 구간 내 최고 위험도
    duration_sec    FLOAT,                          -- ended_at - started_at (초)
    trigger_reasons TEXT[]                          -- 이벤트 시작 당시 증가 요인
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_risk_log_session     ON risk_log     (session_id, frame_index);
CREATE INDEX IF NOT EXISTS idx_risk_summary_session ON risk_summary (session_id, bucket_at);
CREATE INDEX IF NOT EXISTS idx_risk_event_session   ON risk_event   (session_id, started_at);
"""


class RiskLogPipeline:
    """
    yolo_service.py의 위험도 분석 결과를 PostgreSQL에 저장하는 파이프라인.

    내부적으로 3가지 저장 흐름을 관리함:
    1. risk_log    - 위험도 변화 감지 시 즉시 INSERT
    2. risk_summary - 1초 버퍼를 모아서 집계 후 INSERT
    3. risk_event  - 위험 구간 시작/종료를 감지하여 INSERT / UPDATE
    """

    def __init__(self, video_source: str):
        self._conn: Optional[psycopg2.extensions.connection] = None
        self._cursor = None

        # 세션 식별자: 파일명 + 시작 시각
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        basename = os.path.splitext(os.path.basename(video_source))[0]
        self.session_id = f"{basename}_{ts}"
        self._session_started_at = datetime.datetime.now(tz=datetime.timezone.utc)

        # risk_log 상태
        self._last_logged_risk: Optional[float] = None

        # risk_summary 버퍼
        self._summary_buffer: deque = deque()  # (risk_score, increase_reasons, decrease_reasons)
        self._summary_bucket_start: Optional[float] = None  # 현재 버킷 시작 time.time()
        self._summary_bucket_elapsed: float = 0.0

        # risk_event 상태
        self._event_active: bool = False
        self._event_id: Optional[int] = None
        self._event_start_frame: int = 0
        self._event_start_elapsed: float = 0.0
        self._event_peak_risk: int = 0
        self._event_trigger_reasons: list = []

        print(f"[Pipeline] 세션 ID: {self.session_id}")

    # ==============================
    # 연결 및 초기화
    # ==============================

    def connect(self):
        try:
            self._conn = psycopg2.connect(**_get_db_config())
            self._conn.autocommit = False
            self._cursor = self._conn.cursor()
            self._cursor.execute(CREATE_TABLES_SQL)
            self._conn.commit()
            config = _get_db_config()
            print(f"[Pipeline] PostgreSQL 연결 성공: {config['host']}:{config['port']}/{config['dbname']}")
        except Exception as e:
            print(f"[Pipeline] DB 연결 실패 상세: {type(e).__name__}: {e}")  # ← 이 줄로 교체
            raise

    # ==============================
    # 메인 진입점: 매 프레임마다 호출
    # ==============================

    def push(
        self,
        frame_index: int,
        fps: float,
        risk_score: float,
        increase_reasons: list,
        decrease_reasons: list,
        detail: str = "",
    ):
        """
        yolo_service.py 루프 안에서 매 프레임마다 호출.
        내부 조건에 따라 risk_log / risk_summary / risk_event를 선택적으로 저장.
        """
        if self._conn is None:
            return

        elapsed_sec = frame_index / fps if fps > 0 else 0.0
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        risk_int = int(risk_score)

        # 1) risk_log: 위험도 변화 ±RISK_LOG_DELTA 이상일 때만 저장
        self._handle_risk_log(
            frame_index, elapsed_sec, risk_int,
            increase_reasons, decrease_reasons, detail, now,
        )

        # 2) risk_summary: 1초 버퍼 집계
        self._handle_summary_buffer(
            elapsed_sec, risk_int, increase_reasons, decrease_reasons, now,
        )

        # 3) risk_event: 위험 구간 추적
        self._handle_risk_event(
            frame_index, elapsed_sec, risk_int, increase_reasons, now,
        )

    # ==============================
    # 1) risk_log 처리
    # ==============================

    def _handle_risk_log(
        self,
        frame_index, elapsed_sec, risk_int,
        increase_reasons, decrease_reasons, detail, now,
    ):
        if self._last_logged_risk is None:
            # 첫 프레임은 무조건 저장
            delta = 0
        else:
            delta = risk_int - int(self._last_logged_risk)
            if abs(delta) < RISK_LOG_DELTA:
                return  # 변화 없으면 저장 안 함

        prev_risk = int(self._last_logged_risk) if self._last_logged_risk is not None else 0

        self._cursor.execute(
            """
            INSERT INTO risk_log
                (session_id, recorded_at, frame_index, elapsed_sec,
                 risk_score, prev_risk_score, delta,
                 increase_reasons, decrease_reasons, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.session_id,
                now,
                frame_index,
                round(elapsed_sec, 3),
                risk_int,
                prev_risk,
                delta,
                increase_reasons or [],
                decrease_reasons or [],
                detail,
            ),
        )
        self._conn.commit()
        self._last_logged_risk = risk_int

    # ==============================
    # 2) risk_summary 처리
    # ==============================

    def _handle_summary_buffer(
        self, elapsed_sec, risk_int, increase_reasons, decrease_reasons, now,
    ):
        current_time = time.time()

        # 첫 프레임: 버킷 시작
        if self._summary_bucket_start is None:
            self._summary_bucket_start = current_time
            self._summary_bucket_elapsed = elapsed_sec

        self._summary_buffer.append({
            "risk": risk_int,
            "increase": increase_reasons,
            "decrease": decrease_reasons,
        })

        # 1초 경과 시 집계 후 저장
        if current_time - self._summary_bucket_start >= SUMMARY_INTERVAL_SEC:
            self._flush_summary(now)

    def _flush_summary(self, now):
        if not self._summary_buffer:
            return

        risks = [item["risk"] for item in self._summary_buffer]
        all_increase = [r for item in self._summary_buffer for r in item["increase"]]
        all_decrease = [r for item in self._summary_buffer for r in item["decrease"]]

        # 가장 많이 등장한 요인 추출
        top_inc = self._most_common(all_increase)
        top_dec = self._most_common(all_decrease)

        bucket_at = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=SUMMARY_INTERVAL_SEC)

        self._cursor.execute(
            """
            INSERT INTO risk_summary
                (session_id, bucket_at, elapsed_sec,
                 avg_risk, max_risk, min_risk, frame_count,
                 any_increase, any_decrease,
                 top_increase_reason, top_decrease_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.session_id,
                bucket_at,
                round(self._summary_bucket_elapsed, 3),
                round(sum(risks) / len(risks), 2),
                max(risks),
                min(risks),
                len(risks),
                len(all_increase) > 0,
                len(all_decrease) > 0,
                top_inc,
                top_dec,
            ),
        )
        self._conn.commit()

        self._summary_buffer.clear()
        self._summary_bucket_start = time.time()
        self._summary_bucket_elapsed = self._summary_bucket_elapsed + SUMMARY_INTERVAL_SEC

    # ==============================
    # 3) risk_event 처리
    # ==============================

    def _handle_risk_event(
        self, frame_index, elapsed_sec, risk_int, increase_reasons, now,
    ):
        if risk_int >= RISK_EVENT_THRESHOLD:
            if not self._event_active:
                # 위험 이벤트 시작
                self._event_active = True
                self._event_start_frame = frame_index
                self._event_start_elapsed = elapsed_sec
                self._event_peak_risk = risk_int
                self._event_trigger_reasons = list(increase_reasons)

                self._cursor.execute(
                    """
                    INSERT INTO risk_event
                        (session_id, started_at, start_frame, start_elapsed,
                         peak_risk, trigger_reasons)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        self.session_id,
                        now,
                        frame_index,
                        round(elapsed_sec, 3),
                        risk_int,
                        increase_reasons or [],
                    ),
                )
                self._event_id = self._cursor.fetchone()[0]
                self._conn.commit()

            else:
                # 이벤트 진행 중: peak 갱신
                if risk_int > self._event_peak_risk:
                    self._event_peak_risk = risk_int
                    self._cursor.execute(
                        "UPDATE risk_event SET peak_risk = %s WHERE id = %s",
                        (risk_int, self._event_id),
                    )
                    self._conn.commit()

        else:
            if self._event_active:
                # 위험 이벤트 종료
                duration = elapsed_sec - self._event_start_elapsed

                self._cursor.execute(
                    """
                    UPDATE risk_event
                    SET ended_at     = %s,
                        end_frame    = %s,
                        end_elapsed  = %s,
                        duration_sec = %s,
                        peak_risk    = %s
                    WHERE id = %s
                    """,
                    (
                        now,
                        frame_index,
                        round(elapsed_sec, 3),
                        round(duration, 3),
                        self._event_peak_risk,
                        self._event_id,
                    ),
                )
                self._conn.commit()

                print(
                    f"[Pipeline] 위험 이벤트 종료 | "
                    f"id={self._event_id}, "
                    f"지속={duration:.1f}초, "
                    f"최고위험도={self._event_peak_risk}"
                )

                self._event_active = False
                self._event_id = None

    # ==============================
    # 유틸리티
    # ==============================

    @staticmethod
    def _most_common(lst: list) -> Optional[str]:
        if not lst:
            return None
        return max(set(lst), key=lst.count)

    # ==============================
    # 종료 처리
    # ==============================

    def flush_and_close(self):
        """영상 처리 완료 후 반드시 호출. 미완료 이벤트/버퍼를 처리하고 연결 종료."""
        if self._conn is None:
            return

        now = datetime.datetime.now(tz=datetime.timezone.utc)

        # 남은 summary 버퍼 저장
        self._flush_summary(now)

        # 미완료 이벤트 처리 (영상이 위험 구간에서 끝난 경우)
        if self._event_active and self._event_id is not None:
            self._cursor.execute(
                """
                UPDATE risk_event
                SET ended_at     = %s,
                    end_frame    = %s,
                    end_elapsed  = %s,
                    peak_risk    = %s
                WHERE id = %s
                """,
                (
                    now,
                    self._event_start_frame,
                    self._event_start_elapsed,
                    self._event_peak_risk,
                    self._event_id,
                ),
            )
            self._conn.commit()

        self._cursor.close()
        self._conn.close()
        print(f"[Pipeline] DB 연결 종료. 세션: {self.session_id}")