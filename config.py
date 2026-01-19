"""
설정 파일
"""
import os

# 카카오맵 API 키 - 환경변수에서 읽기 (필수, 없으면 에러)
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
if not KAKAO_API_KEY:
    raise ValueError("KAKAO_API_KEY 환경변수가 필요합니다")
KAKAO_DIRECTIONS_API_URL = "https://apis-navi.kakaomobility.com/v1/directions"

# 시스템 기본 설정
WORK_START_TIME = 9  # 오전 9시
WORK_END_TIME = 18   # 오후 6시
MAX_ASSIGNMENT_DAYS = 3  # 기사당 최대 배정 일수
SYSTEM_BUFFER_TIME = 30  # 시스템 기본 버퍼 시간 (분)

# 시간 슬롯 정의 (시간 미지정 작업 판단용)
TIME_SLOTS = {
    "morning": {"start": 9, "end": 12},
    "afternoon": {"start": 12, "end": 18},
    "full_day": {"start": 9, "end": 18}
}

# 서비스별 기본 소요시간 (분) - duration_minutes 누락 시 사용
DEFAULT_DURATION_BY_SERVICE = {
    "입주청소": 180,
    "이사청소": 180,
    "에어컨청소": 120,
    "청소청소": 150,
    # 추가 서비스는 여기에 추가
}
