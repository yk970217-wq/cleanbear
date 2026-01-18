"""
데이터 누락 시 Fallback 처리 모듈
"""
from typing import Optional, Tuple
from models import Task, WorkerState
import config


class FallbackHandler:
    """Fallback 처리 핸들러"""
    
    @staticmethod
    def apply_fallbacks(
        task: Task,
        worker_state: Optional[WorkerState] = None
    ) -> Tuple[Task, bool]:
        """
        Task에 fallback 적용
        
        Returns:
            (처리된 Task, 배정 가능 여부)
            배정 불가능 시: (Task, False)
        """
        fallback_used = False
        fallback_details = []
        
        # 1. 서비스 종류 체크 (필수, fallback 불가)
        if not task.service_type or task.service_type.strip() == "":
            task.fallback_used = True
            task.fallback_details = ["service_type_missing"]
            return task, False
        
        # 2. 작업 소요시간 (duration_minutes)
        if task.duration_minutes is None or task.duration_minutes <= 0:
            default_duration = config.DEFAULT_DURATION_BY_SERVICE.get(task.service_type)
            if default_duration:
                task.duration_minutes = default_duration
                fallback_used = True
                fallback_details.append("duration_minutes: default_duration")
            else:
                task.fallback_used = True
                task.fallback_details = ["duration_missing"]
                return task, False
        
        # 3. 기준 위치 (current_location)
        if not task.current_location or task.current_location.strip() == "":
            if worker_state:
                if worker_state.last_location:
                    task.current_location = worker_state.last_location
                    fallback_used = True
                    fallback_details.append("current_location: last_location")
                else:
                    task.current_location = worker_state.worker.home_address
                    fallback_used = True
                    fallback_details.append("current_location: home_address")
            else:
                # worker_state가 없으면 빈 문자열로 처리 (나중에 처리)
                task.current_location = ""
        
        # 4. 시간 지정 여부 (time_fixed)
        if task.fixed_start_time is None or task.fixed_start_time == "":
            # false로 처리 (시간 미지정)
            task.fixed_start_time = None
            # fallback 기록은 하지 않음 (기본값이므로)
        
        # 5. 시간 슬롯 (slot_type)
        if not task.slot_type or task.slot_type.strip() == "":
            task.slot_type = "ALLDAY"
            fallback_used = True
            fallback_details.append("slot_type: ALLDAY")
        
        # 6. 초과근무 가능 여부 (overtime_allowed)
        if task.overtime_allowed is None:
            if worker_state:
                task.overtime_allowed = worker_state.worker.overtime_enabled
                fallback_used = True
                fallback_details.append(f"overtime_allowed: worker.overtime_enabled ({worker_state.worker.overtime_enabled})")
            else:
                task.overtime_allowed = False
                fallback_used = True
                fallback_details.append("overtime_allowed: false")
        
        task.fallback_used = fallback_used
        task.fallback_details = fallback_details
        
        return task, True
    
    @staticmethod
    def get_worker_start_location(
        task: Task,
        worker_state: WorkerState
    ) -> str:
        """
        기사의 시작 위치 결정 (fallback 우선순위 적용)
        
        Returns:
            시작 위치 주소
        """
        # 1. job.current_location
        if task.current_location and task.current_location.strip():
            return task.current_location
        
        # 2. last_location
        if worker_state.last_location:
            return worker_state.last_location
        
        # 3. home_address
        return worker_state.worker.home_address


def apply_fallback_to_task_before_matching(
    task: Task
) -> Tuple[Task, bool]:
    """
    기사 매칭 전에 작업에 fallback 적용 (서비스 종류, 소요시간만 체크)
    
    Returns:
        (처리된 Task, 배정 가능 여부)
    """
    fallback_used = False
    fallback_details = []
    
    # 1. 서비스 종류 체크 (필수)
    if not task.service_type or task.service_type.strip() == "":
        task.fallback_used = True
        task.fallback_details = ["service_type_missing"]
        return task, False
    
    # 2. 작업 소요시간
    if task.duration_minutes is None or task.duration_minutes <= 0:
        default_duration = config.DEFAULT_DURATION_BY_SERVICE.get(task.service_type)
        if default_duration:
            task.duration_minutes = default_duration
            fallback_used = True
            fallback_details.append("duration_minutes: default_duration")
        else:
            task.fallback_used = True
            task.fallback_details = ["duration_missing"]
            return task, False
    
    # 3. 시간 지정 여부
    if task.fixed_start_time is None or task.fixed_start_time == "":
        task.fixed_start_time = None  # 시간 미지정으로 처리
    
    # 4. 시간 슬롯
    if not task.slot_type or task.slot_type.strip() == "":
        task.slot_type = "ALLDAY"
        fallback_used = True
        fallback_details.append("slot_type: ALLDAY")
    
    task.fallback_used = fallback_used
    task.fallback_details = fallback_details
    
    return task, True
