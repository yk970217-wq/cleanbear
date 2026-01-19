"""
배정 알고리즘 핵심 로직 (Make 데이터 계약 준수)
"""
from typing import List, Optional, Tuple, Dict
from datetime import date, datetime, timedelta
from models import (
    Job, Technician, TechnicianState, TechnicianWorkingState,
    SystemRules, Assignment
)
from kakao_api import calculate_travel_time


class Scheduler:
    """작업 배정 스케줄러"""
    
    def __init__(
        self,
        technicians: List[Technician],
        technician_states: List[TechnicianState],
        system_rules: SystemRules
    ):
        """초기화"""
        self.technicians = technicians
        self.system_rules = system_rules
        self.working_states: Dict[str, TechnicianWorkingState] = {}
        
        # 기사별 상태 초기화 (technician_states에서 복원 또는 기본값 사용)
        state_dict = {ts.technician_id: ts for ts in technician_states}
        
        for technician in technicians:
            state = state_dict.get(technician.technician_id)
            
            # 현재 위치 결정: technician_state의 last_address 또는 home_address
            if state and state.last_address:
                current_address = state.last_address
            else:
                current_address = technician.home_address
            
            self.working_states[technician.technician_id] = TechnicianWorkingState(
                technician=technician,
                current_address=current_address,
                last_work_end_time=state.last_end_time if state else None
            )
    
    def assign_jobs(self, jobs: List[Job]) -> Tuple[List[Assignment], List[Assignment], List[Assignment]]:
        """
        작업 목록을 배정
        
        Args:
            jobs: 배정할 작업 목록
        
        Returns:
            (assigned_jobs, failed_jobs, deferred_jobs)
        """
        assigned_jobs = []
        failed_jobs = []
        deferred_jobs = []
        
        for job in jobs:
            if job.error_reason:
                assignment = self._create_failed_assignment(job, job.error_reason)
                failed_jobs.append(assignment)
                continue
            
            result = self._assign_single_job(job)
            
            if result is None:
                # 3일 제한으로 배정 못한 경우
                assignment = self._create_failed_assignment(job, "MAX_PREASSIGN_DAYS_EXCEEDED")
                deferred_jobs.append(assignment)
            elif result.status == "failed":
                failed_jobs.append(result)
            else:
                assigned_jobs.append(result)
        
        return assigned_jobs, failed_jobs, deferred_jobs
    
    def _assign_single_job(self, job: Job) -> Optional[Assignment]:
        """
        단일 작업 배정
        
        Args:
            job: 배정할 작업
        
        Returns:
            Assignment 또는 None (3일 제한으로 배정 못한 경우)
        """
        # 1. 서비스 가능한 기사 필터링
        available_working_states = [
            ws for ws in self.working_states.values()
            if ws.technician.can_handle_service(job.service_type)
        ]
        
        if not available_working_states:
            # 서비스 가능한 기사가 없는 경우 - 실패 처리
            return self._create_failed_assignment(job, "서비스를 처리할 수 있는 기사가 없음")
        
        # 2. max_preassign_days 제한 체크로 필터링
        assignable_states = [
            ws for ws in available_working_states
            if ws.can_assign_date(job.date, self.system_rules.max_preassign_days)
        ]
        
        if not assignable_states:
            # max_preassign_days가 꽉 찬 경우 - None 반환 (deferred_jobs로 처리)
            return None
        
        # 3. 각 기사에 대한 점수 계산 (거리/시간 기준)
        best_working_state = None
        best_score = float('inf')
        best_travel_time = 0.0
        
        for working_state in assignable_states:
            # 주소 기반 거리 계산
            travel_time = calculate_travel_time(
                working_state.current_address,
                job.address
            )
            
            # 시간 체크
            can_fit, score, error_reason = self._check_time_fit(working_state, job, travel_time)
            
            if can_fit and score < best_score:
                best_score = score
                best_working_state = working_state
                best_travel_time = travel_time
            elif error_reason and not job.error_reason:
                # 첫 번째 에러 원인 기록
                job.error_reason = error_reason
        
        if best_working_state is None:
            # 시간상 배정 불가능한 경우
            reason = job.error_reason or "시간상 배정 불가능"
            return self._create_failed_assignment(job, reason)
        
        # 4. 배정 생성
        assignment = self._create_assignment(
            job=job,
            working_state=best_working_state,
            travel_time=best_travel_time
        )
        
        # 5. 기사 상태 업데이트
        self._update_working_state(best_working_state, assignment)
        
        return assignment
    
    def _check_time_fit(
        self,
        working_state: TechnicianWorkingState,
        job: Job,
        travel_time: float
    ) -> Tuple[bool, float, Optional[str]]:
        """
        작업이 시간상 들어갈 수 있는지 체크
        
        Returns:
            (가능 여부, 점수, 에러 원인)
        """
        # 같은 날짜의 기존 배정 목록
        existing_assignments = working_state.get_assignments_for_date(job.date)
        
        if job.is_time_fixed() and job.fixed_start_time:
            # 시간 고정 작업
            return self._check_fixed_time_fit(working_state, job, travel_time, existing_assignments)
        else:
            # 시간 미지정 작업 - 슬롯만 체크
            return self._check_undefined_time_fit(job, travel_time, existing_assignments, working_state.technician)
    
    def _check_fixed_time_fit(
        self,
        working_state: TechnicianWorkingState,
        job: Job,
        travel_time: float,
        existing_assignments: List[Assignment]
    ) -> Tuple[bool, float, Optional[str]]:
        """시간 고정 작업의 시간 체크"""
        # fixed_start_time 파싱
        try:
            time_parts = job.fixed_start_time.split(':')
            start_hour = int(time_parts[0])
            start_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            task_start_minutes = start_hour * 60 + start_minute
        except (ValueError, IndexError, AttributeError):
            return False, float('inf'), "시간 형식 오류"
        
        # 작업 종료 시간 계산
        work_start_hour, work_start_min = map(int, self.system_rules.work_start.split(':'))
        work_end_hour, work_end_min = map(int, self.system_rules.work_end.split(':'))
        work_start_minutes = work_start_hour * 60 + work_start_min
        work_end_minutes = work_end_hour * 60 + work_end_min
        
        task_end_minutes = task_start_minutes + job.duration_min + self.system_rules.default_buffer_min
        
        # 근무 시간 체크 (18시 초과 여부)
        if task_end_minutes > work_end_minutes:
            if not working_state.technician.overtime_allowed:
                return False, float('inf'), "OVERTIME_NOT_ALLOWED"
        
        # 기존 배정과의 충돌 체크
        for existing in existing_assignments:
            if existing.job.is_time_fixed() and existing.job.fixed_start_time:
                try:
                    existing_parts = existing.job.fixed_start_time.split(':')
                    existing_start_hour = int(existing_parts[0])
                    existing_start_minute = int(existing_parts[1]) if len(existing_parts) > 1 else 0
                    existing_start_minutes = existing_start_hour * 60 + existing_start_minute
                    existing_end_minutes = existing_start_minutes + existing.job.duration_min + self.system_rules.default_buffer_min
                    
                    # 시간 겹침 체크
                    if not (task_end_minutes <= existing_start_minutes or task_start_minutes >= existing_end_minutes):
                        return False, float('inf'), "시간 충돌"
                except (ValueError, IndexError):
                    continue
            
            # 같은 날 이전 작업이 있는 경우 이동 시간 고려
            if existing.estimated_end_time:
                try:
                    existing_end_parts = existing.estimated_end_time.split(':')
                    existing_end_h = int(existing_end_parts[0])
                    existing_end_m = int(existing_end_parts[1]) if len(existing_end_parts) > 1 else 0
                    existing_end_minutes = existing_end_h * 60 + existing_end_m
                    
                    if existing_end_minutes + travel_time > task_start_minutes:
                        return False, float('inf'), "이동 시간 부족"
                except (ValueError, IndexError):
                    pass
        
        # 점수 = 이동 시간
        return True, travel_time, None
    
    def _check_undefined_time_fit(
        self,
        job: Job,
        travel_time: float,
        existing_assignments: List[Assignment],
        technician: Technician
    ) -> Tuple[bool, float, Optional[str]]:
        """시간 미지정 작업의 슬롯 체크 + overtime 체크"""
        # 작업 소요시간 + 버퍼
        total_duration = job.duration_min + self.system_rules.default_buffer_min
        
        # slot_type fallback: 없으면 ALLDAY
        slot_type = job.slot_type or "ALLDAY"
        
        # work_start, work_end 파싱
        work_start_hour, work_start_min = map(int, self.system_rules.work_start.split(':'))
        work_end_hour, work_end_min = map(int, self.system_rules.work_end.split(':'))
        work_start_minutes = work_start_hour * 60 + work_start_min
        work_end_minutes = work_end_hour * 60 + work_end_min
        
        # 슬롯 크기 계산
        if slot_type == "MORNING":
            slot_duration = (12 * 60) - work_start_minutes
            max_end_minutes = 12 * 60
        elif slot_type == "AFTERNOON":
            slot_duration = work_end_minutes - (12 * 60)
            max_end_minutes = work_end_minutes
        else:  # ALLDAY
            slot_duration = work_end_minutes - work_start_minutes
            max_end_minutes = work_end_minutes
        
        # 슬롯에 들어가는지 확인
        if total_duration > slot_duration:
            return False, float('inf'), "슬롯 크기 초과"
        
        # 18시 초과 여부 체크 (가장 늦게 시작하는 경우)
        latest_possible_start = max_end_minutes - total_duration
        latest_possible_end = latest_possible_start + total_duration
        
        if latest_possible_end > work_end_minutes:
            if not technician.overtime_allowed:
                return False, float('inf'), "OVERTIME_NOT_ALLOWED"
        
        # 점수 = 이동 시간 (시간 미지정이어도 거리 순으로 배정)
        return True, travel_time, None
    
    def _create_assignment(
        self,
        job: Job,
        working_state: TechnicianWorkingState,
        travel_time: float
    ) -> Assignment:
        """배정 객체 생성"""
        assignment = Assignment(
            job=job,
            technician=working_state.technician,
            travel_time_minutes=travel_time
        )
        
        # time_fixed 처리
        if job.is_time_fixed() and job.fixed_start_time:
            assignment.status = "assigned"
            assignment.estimated_start_time = job.fixed_start_time
            
            # 종료 시간 계산
            try:
                time_parts = job.fixed_start_time.split(':')
                start_hour = int(time_parts[0])
                start_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                start_minutes = start_hour * 60 + start_minute
                end_minutes = start_minutes + job.duration_min + self.system_rules.default_buffer_min
                assignment.estimated_end_time = self._minutes_to_time(end_minutes)
            except (ValueError, IndexError):
                pass
        else:
            assignment.status = "time_undefined"
        
        return assignment
    
    def _create_failed_assignment(self, job: Job, reason: str) -> Assignment:
        """배정 실패 케이스"""
        # 임시 Technician 객체 생성
        dummy_technician = Technician(
            technician_id="FAILED",
            home_address="",
            service_types=[],
            overtime_allowed=False
        )
        
        job.error_reason = reason
        assignment = Assignment(
            job=job,
            technician=dummy_technician,
            status="failed",
            memo=reason
        )
        
        return assignment
    
    def _update_working_state(
        self,
        working_state: TechnicianWorkingState,
        assignment: Assignment
    ):
        """기사 상태 업데이트"""
        # 배정 추가
        working_state.add_assignment(assignment)
        
        # 위치 업데이트 (작업 위치로)
        working_state.current_address = assignment.job.address
        
        # 마지막 작업 시간은 estimated_end_time이 있을 때만 업데이트
        if assignment.estimated_end_time:
            working_state.last_work_end_time = assignment.estimated_end_time
            working_state.last_work_date = assignment.job.date
    
    def _minutes_to_time(self, minutes: int) -> str:
        """분 단위를 HH:MM 형식으로 변환"""
        hour = minutes // 60
        minute = minutes % 60
        return f"{hour:02d}:{minute:02d}"