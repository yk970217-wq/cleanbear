"""
데이터 모델 정의 - Make 데이터 계약에 맞춘 구조
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, date


@dataclass
class SystemRules:
    """시스템 운영 정책"""
    work_start: str  # HH:mm 형식
    work_end: str  # HH:mm 형식
    max_preassign_days: int
    default_buffer_min: int


@dataclass
class Job:
    """작업 정보 (Make 계약)"""
    job_id: str  # 필수
    service_type: str  # 필수
    lat: float  # 필수
    lng: float  # 필수
    date: date  # 필수 (YYYY-MM-DD)
    duration_min: int  # 필수 (분)
    
    # 선택 필드
    time_fixed: Optional[bool] = None
    fixed_start_time: Optional[str] = None  # HH:MM 형식 (time_fixed가 True일 때 필수)
    slot_type: Optional[str] = None  # MORNING | AFTERNOON | ALLDAY
    
    # Fallback 관련
    fallback_used: bool = False
    fallback_details: List[str] = field(default_factory=list)
    error_reason: Optional[str] = None  # 배정 실패 시 이유
    
    def is_time_fixed(self) -> bool:
        """시간이 지정되었는지 여부"""
        return self.time_fixed is True


@dataclass
class Technician:
    """기사 정보 (Make 계약)"""
    technician_id: str  # 필수
    home_lat: float  # 필수
    home_lng: float  # 필수
    service_types: List[str]  # 필수
    overtime_allowed: bool  # 필수
    
    def can_handle_service(self, service_type: str) -> bool:
        """특정 서비스 종류를 처리할 수 있는지"""
        return service_type in self.service_types


@dataclass
class TechnicianState:
    """기사 현재 상태 (Make 계약 - 선택적)"""
    technician_id: str
    last_lat: Optional[float] = None
    last_lng: Optional[float] = None
    last_end_time: Optional[str] = None  # ISO string


@dataclass
class RouteInfo:
    """경로 정보 (카카오맵 API 결과)"""
    distance_meters: int  # 거리 (미터)
    duration_seconds: int  # 소요 시간 (초)
    duration_minutes: float  # 소요 시간 (분)
    
    @classmethod
    def from_kakao_api(cls, api_response: dict):
        """카카오맵 API 응답에서 RouteInfo 생성"""
        routes = api_response.get("routes", [])
        if not routes:
            raise ValueError("API 응답에 routes가 없습니다")
        
        summary = routes[0].get("summary", {})
        distance = summary.get("distance", 0)  # 미터
        duration = summary.get("duration", 0)  # 밀리초
        
        return cls(
            distance_meters=distance,
            duration_seconds=int(duration / 1000),
            duration_minutes=round(duration / 1000 / 60, 1)
        )


@dataclass
class Assignment:
    """배정 정보"""
    job: Job
    technician: Technician
    estimated_start_time: Optional[str] = None  # HH:MM
    estimated_end_time: Optional[str] = None  # HH:MM
    travel_time_minutes: float = 0  # 이동 시간 (분)
    status: str = "assigned"  # assigned, time_undefined, failed
    memo: str = ""
    
    def to_dict(self) -> dict:
        """Make 연동용 딕셔너리로 변환"""
        result = {
            "job_id": self.job.job_id,
            "technician_id": self.technician.technician_id,
            "date": self.job.date.isoformat(),
            "service_type": self.job.service_type,
            "lat": self.job.lat,
            "lng": self.job.lng,
            "duration_min": self.job.duration_min,
            "travel_time_minutes": round(self.travel_time_minutes, 1),
            "status": self.status,
            "fallback_used": self.job.fallback_used,
            "fallback_details": list(self.job.fallback_details) if self.job.fallback_details else [],
        }
        
        # 에러 정보
        if self.job.error_reason:
            result["error_reason"] = self.job.error_reason
        
        # 시간 처리
        if self.job.is_time_fixed():
            result["start_time"] = self.estimated_start_time
            result["end_time"] = self.estimated_end_time
            result["time_status"] = "fixed"
        else:
            result["start_time"] = None
            result["end_time"] = None
            result["time_status"] = "undefined"
            if self.status != "failed":
                result["memo"] = "시간 미정 - 전날 통화 조율"
        
        # 메모 병합
        if self.memo:
            if result.get("memo"):
                result["memo"] = f"{self.memo} / {result['memo']}"
            else:
                result["memo"] = self.memo
            
        return result


@dataclass
class TechnicianWorkingState:
    """기사 작업 상태 (배정 진행 중)"""
    technician: Technician
    current_lat: float  # 현재 위치 (위도)
    current_lng: float  # 현재 위치 (경도)
    assignments_by_date: dict = field(default_factory=dict)  # 날짜별 배정 목록 {date: [Assignment]}
    last_work_end_time: Optional[str] = None  # 마지막 작업 종료 시간 (HH:MM)
    last_work_date: Optional[date] = None  # 마지막 작업 날짜
    
    def get_assignments_for_date(self, target_date: date) -> List[Assignment]:
        """특정 날짜의 배정 목록 반환"""
        return self.assignments_by_date.get(target_date, [])
    
    def add_assignment(self, assignment: Assignment):
        """배정 추가"""
        date_key = assignment.job.date
        if date_key not in self.assignments_by_date:
            self.assignments_by_date[date_key] = []
        self.assignments_by_date[date_key].append(assignment)
    
    def get_assigned_days_count(self) -> int:
        """배정된 날짜 수 반환"""
        return len(self.assignments_by_date)
    
    def can_assign_date(self, target_date: date, max_preassign_days: int) -> bool:
        """해당 날짜에 배정 가능한지 (max_preassign_days 제한 체크)"""
        assigned_days = sorted(self.assignments_by_date.keys())
        if len(assigned_days) < max_preassign_days:
            return True
        
        # 최대 일수가 모두 채워진 경우, 가장 늦은 날짜 이후만 가능
        if assigned_days:
            latest_date = max(assigned_days)
            return target_date > latest_date
        
        return True
