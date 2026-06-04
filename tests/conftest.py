"""
pytest 공통 설정 파일

markers 정의:
- unit: 외부 의존성 없는 순수 단위 테스트 (기본 실행 대상)
- integration: 실제 DB 연결이 필요한 통합 테스트 (별도 실행)
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: 단위 테스트 (DB 연결 불필요)")
    config.addinivalue_line("markers", "integration: 통합 테스트 (실제 DB 필요)")