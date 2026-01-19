"""
청소 기사 자동 배정 시스템 - HTTP API 서버 (FastAPI)

Make에서 HTTP 요청으로 호출되는 엔트리 포인트
JSON 입력을 받아서 배정 결과를 JSON으로 출력
"""
import json
import os
import threading
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import date
from typing import List, Dict, Any, Tuple, Optional
from models import Job, Technician, TechnicianState, SystemRules, Assignment
from scheduler import Scheduler
from google_sheets import GoogleSheetsClient
from kakao_api import calculate_travel_time

app = FastAPI()

# 전역 기사 저장소 (메모리)
_technicians_storage: List[Technician] = []
_technician_states_storage: Dict[str, TechnicianState] = {}  # technician_id -> state
_technicians_loaded: bool = False
_technicians_lock = threading.Lock()  # 스레드 안전성을 위한 락


# ==================== 필수 라우트 ====================

@app.get("/")
def root():
    """루트 엔드포인트"""
    return "server running"


@app.get("/health")
def health():
    """헬스 체크 엔드포인트"""
    with _technicians_lock:
        technicians_count = len(_technicians_storage)
        loaded = _technicians_loaded
    
    return {
        "status": "ok",
        "service": "기사 배정 시스템",
        "technicians_loaded": loaded,
        "technicians_count": technicians_count
    }


@app.post("/jobs")
async def create_job(request: Request):
    """
    단일 작업 배정 API
    
    요구사항: JSON Body를 그대로 받아서 로그 출력 후 {"ok": true} 반환
    기존 배정 로직도 유지
    """
    try:
        # JSON Body 읽기
        job_data = await request.json()
        print(f"POST /jobs 요청: {json.dumps(job_data, ensure_ascii=False, indent=2)}")
        
        # 기존 배정 로직 실행
        try:
            # 기사 목록 확인
            with _technicians_lock:
                if not _technicians_loaded or not _technicians_storage:
                    # 기사 목록이 없어도 요구사항에 따라 {"ok": true} 반환
                    print("경고: 기사 목록이 로드되지 않았습니다")
                    return {"ok": True}
                
                technicians = _technicians_storage.copy()
            
            # job 필드 확인
            if "job" not in job_data:
                return {"ok": True}
            
            job_info = job_data["job"]
            
            # Job 객체 생성
            try:
                preferred_date = date.fromisoformat(job_info.get("preferred_date", ""))
            except:
                return {"ok": True}
            
            job = Job(
                job_id=f"JOB_{preferred_date.isoformat()}_{int(time.time())}",
                service_type=job_info.get("service_type", ""),
                address=job_info.get("address", ""),
                date=preferred_date,
                duration_min=int(job_info.get("duration_min", 0)),
                time_fixed=job_info.get("time_fixed", False),
                fixed_start_time=job_info.get("fixed_start_time") or None,
                slot_type=job_info.get("slot_type")
            )
            
            if not job.service_type or not job.address:
                return {"ok": True}
            
            # 배정 로직 실행
            available_technicians = [
                tech for tech in technicians
                if tech.can_handle_service(job.service_type)
            ]
            
            if not available_technicians:
                return {"ok": True}
            
            # priority 정렬
            available_technicians.sort(key=lambda t: t.priority, reverse=True)
            
            # 가장 적합한 기사 선택
            best_technician = None
            best_travel_time = float('inf')
            
            max_priority = available_technicians[0].priority
            priority_group = [t for t in available_technicians if t.priority == max_priority]
            
            for tech in priority_group:
                travel_time = calculate_travel_time(tech.home_address, job.address)
                if travel_time < best_travel_time:
                    best_travel_time = travel_time
                    best_technician = tech
            
            if best_technician:
                print(f"배정 완료: {best_technician.technician_id} ({best_technician.name})")
        
        except Exception as e:
            print(f"배정 로직 실행 중 오류: {str(e)}")
        
        # 요구사항: {"ok": true} 반환
        return {"ok": True}
        
    except Exception as e:
        print(f"POST /jobs 오류: {str(e)}")
        return {"ok": True}


# ==================== 기존 로직 유지 ====================

def parse_json_input(json_data: Dict[str, Any]) -> Tuple[List[Job], List[Technician], List[Dict[str, Any]], List[TechnicianState], SystemRules]:
    """
    Make에서 전달받은 JSON 데이터 파싱 (데이터 계약 준수)
    
    입력 계약:
    {
        "jobs": [...],
        "technicians": [...],
        "technician_states": [...],  // 선택적
        "system_rules": {...}
    }
    """
    jobs: List[Job] = []
    technicians: List[Technician] = []
    skipped_technicians: List[Dict[str, Any]] = []
    technician_states: List[TechnicianState] = []
    
    # system_rules 파싱 (필수)
    rules_data = json_data.get("system_rules", {})
    if not rules_data:
        raise ValueError("system_rules가 없습니다")
    
    system_rules = SystemRules(
        work_start=rules_data.get("work_start", "09:00"),
        work_end=rules_data.get("work_end", "18:00"),
        max_preassign_days=int(rules_data.get("max_preassign_days", 3)),
        default_buffer_min=int(rules_data.get("default_buffer_min", 30))
    )
    
    # jobs 파싱
    for job_data in json_data.get("jobs", []):
        # 필수 필드 체크
        required_fields = ["job_id", "service_type", "address", "date", "duration_min"]
        missing_fields = [f for f in required_fields if job_data.get(f) is None]
        
        if missing_fields:
            try:
                job_date = date.fromisoformat(job_data.get("date", "2000-01-01"))
            except:
                job_date = date.today()
            
            job = Job(
                job_id=job_data.get("job_id", "UNKNOWN"),
                service_type=job_data.get("service_type", ""),
                address=job_data.get("address", ""),
                date=job_date,
                duration_min=job_data.get("duration_min", 0),
                error_reason=f"필수 필드 누락: {', '.join(missing_fields)}"
            )
            jobs.append(job)
            continue
        
        # 날짜 파싱
        try:
            job_date = date.fromisoformat(job_data["date"])
        except (ValueError, TypeError):
            job = Job(
                job_id=job_data["job_id"],
                service_type=job_data.get("service_type", ""),
                address=job_data.get("address", ""),
                date=date.today(),
                duration_min=job_data["duration_min"],
                error_reason="날짜 형식 오류"
            )
            jobs.append(job)
            continue
        
        # time_fixed 검증
        time_fixed = job_data.get("time_fixed") if job_data.get("time_fixed") is not None else None
        fixed_start_time = job_data.get("fixed_start_time") or None
        
        if time_fixed is True and (not fixed_start_time or fixed_start_time.strip() == ""):
            job = Job(
                job_id=job_data["job_id"],
                service_type=job_data["service_type"],
                address=job_data.get("address", ""),
                date=job_date,
                duration_min=int(job_data["duration_min"]),
                time_fixed=time_fixed,
                fixed_start_time=fixed_start_time,
                slot_type=job_data.get("slot_type"),
                error_reason="FIXED_TIME_MISSING"
            )
            jobs.append(job)
            continue
        
        # Job 생성
        job = Job(
            job_id=job_data["job_id"],
            service_type=job_data["service_type"],
            address=job_data["address"],
            date=job_date,
            duration_min=int(job_data["duration_min"]),
            time_fixed=time_fixed,
            fixed_start_time=fixed_start_time,
            slot_type=job_data.get("slot_type")
        )
        jobs.append(job)
    
    # technicians 파싱
    for tech_data in json_data.get("technicians", []):
        required_fields = ["technician_id", "home_address", "service_types", "overtime_allowed"]
        missing_fields = [f for f in required_fields if tech_data.get(f) is None]
        
        if missing_fields:
            skipped_technicians.append({
                "technician_id": tech_data.get("technician_id", "UNKNOWN"),
                "reason": f"필수 필드 누락: {', '.join(missing_fields)}",
                "missing_fields": missing_fields
            })
            continue
        
        technician = Technician(
            technician_id=tech_data["technician_id"],
            home_address=tech_data["home_address"],
            service_types=list(tech_data["service_types"]),
            overtime_allowed=bool(tech_data["overtime_allowed"])
        )
        technicians.append(technician)
    
    # technician_states 파싱 (선택적)
    for state_data in json_data.get("technician_states", []):
        if "technician_id" not in state_data:
            continue
        
        state = TechnicianState(
            technician_id=state_data["technician_id"],
            last_address=state_data.get("last_address") or None,
            last_end_time=state_data.get("last_end_time")
        )
        technician_states.append(state)
    
    return jobs, technicians, skipped_technicians, technician_states, system_rules


def format_machine_output(
    assigned_jobs: List[Assignment],
    failed_jobs: List[Assignment],
    deferred_jobs: List[Assignment],
    skipped_technicians: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """기계가 읽을 배정 결과 (Make 연동용)"""
    assigned_dicts = [a.to_dict() for a in assigned_jobs]
    failed_dicts = [a.to_dict() for a in failed_jobs]
    deferred_dicts = [a.to_dict() for a in deferred_jobs]
    
    total = len(assigned_jobs) + len(failed_jobs) + len(deferred_jobs)
    summary = {
        "total_jobs": total,
        "assigned": len(assigned_jobs),
        "failed": len(failed_jobs),
        "deferred": len(deferred_jobs)
    }
    
    result = {
        "success": True,
        "assigned_jobs": assigned_dicts,
        "failed_jobs": failed_dicts,
        "deferred_jobs": deferred_dicts,
        "summary": summary
    }
    
    if skipped_technicians:
        result["skipped_technicians"] = skipped_technicians
    
    return result


def generate_human_message(
    assigned_jobs: List[Assignment],
    failed_jobs: List[Assignment],
    deferred_jobs: List[Assignment],
    skipped_technicians: List[Dict[str, Any]]
) -> str:
    """사람이 읽을 메시지 생성"""
    total = len(assigned_jobs) + len(failed_jobs) + len(deferred_jobs)
    if total == 0:
        return "배정할 작업이 없습니다."
    
    messages = []
    messages.append(f"📋 배정 결과 요약")
    messages.append(f"- 전체 작업: {total}건")
    messages.append(f"- 배정 완료: {len(assigned_jobs)}건")
    messages.append(f"- 배정 실패: {len(failed_jobs)}건")
    messages.append(f"- 3일 제한 초과: {len(deferred_jobs)}건")
    
    if failed_jobs:
        messages.append("")
        messages.append("⚠️ 배정 실패 작업:")
        for assignment in failed_jobs[:5]:
            reason = assignment.job.error_reason or assignment.memo or "배정 실패"
            messages.append(f"  • {assignment.job.job_id}: {reason}")
        
        if len(failed_jobs) > 5:
            messages.append(f"  ... 외 {len(failed_jobs) - 5}건")
    
    if deferred_jobs:
        messages.append("")
        messages.append("⏰ 3일 제한 초과 작업 (다음 배정 단계에서 처리):")
        for assignment in deferred_jobs[:3]:
            messages.append(f"  • {assignment.job.job_id}: {assignment.job.date}")
        
        if len(deferred_jobs) > 3:
            messages.append(f"  ... 외 {len(deferred_jobs) - 3}건")
    
    if skipped_technicians:
        messages.append("")
        messages.append(f"⚠️ 기사 스킵: {len(skipped_technicians)}명 (필수 필드 누락)")
        for skipped in skipped_technicians[:3]:
            messages.append(f"  • {skipped['technician_id']}: {skipped['reason']}")
        
        if len(skipped_technicians) > 3:
            messages.append(f"  ... 외 {len(skipped_technicians) - 3}명")
    
    all_jobs = assigned_jobs + failed_jobs + deferred_jobs
    fallback_used = [a for a in all_jobs if a.job.fallback_used]
    if fallback_used:
        messages.append("")
        messages.append(f"📌 중요: 기본값 사용된 작업 {len(fallback_used)}건")
        messages.append("  (duration_min 누락/0일 때 서비스별 기본값 사용)")
        messages.append("  → 상세는 결과 데이터의 fallback_details 참조")
    
    return "\n".join(messages)


@app.post('/assign')
async def assign_jobs(request: Request):
    """작업 배정 API 엔드포인트"""
    try:
        input_data = await request.json()
        
        if not input_data:
            return JSONResponse(
                status_code=400,
                content={
                    "machine_output": {
                        "success": False,
                        "error": "요청 데이터가 없습니다",
                        "assigned_jobs": [],
                        "failed_jobs": [],
                        "deferred_jobs": [],
                        "summary": {"total_jobs": 0, "assigned": 0, "failed": 0, "deferred": 0}
                    },
                    "human_message": "❌ 오류: 요청 데이터가 없습니다."
                }
            )
        
        jobs, technicians, skipped_technicians, technician_states, system_rules = parse_json_input(input_data)
        
        if not jobs:
            return JSONResponse(
                status_code=400,
                content={
                    "machine_output": {
                        "success": False,
                        "error": "작업 데이터가 없습니다",
                        "assigned_jobs": [],
                        "failed_jobs": [],
                        "deferred_jobs": [],
                        "summary": {"total_jobs": 0, "assigned": 0, "failed": 0, "deferred": 0}
                    },
                    "human_message": "⚠️ 작업 데이터가 없습니다."
                }
            )
        
        if not technicians:
            return JSONResponse(
                status_code=400,
                content={
                    "machine_output": {
                        "success": False,
                        "error": "기사 데이터가 없습니다",
                        "assigned_jobs": [],
                        "failed_jobs": [],
                        "deferred_jobs": [],
                        "summary": {"total_jobs": len(jobs), "assigned": 0, "failed": 0, "deferred": 0}
                    },
                    "human_message": f"⚠️ 기사 데이터가 없습니다. 작업 {len(jobs)}건이 배정되지 않았습니다."
                }
            )
        
        scheduler = Scheduler(technicians, technician_states, system_rules)
        assigned_jobs, failed_jobs, deferred_jobs = scheduler.assign_jobs(jobs)
        
        machine_output = format_machine_output(assigned_jobs, failed_jobs, deferred_jobs, skipped_technicians)
        human_message = generate_human_message(assigned_jobs, failed_jobs, deferred_jobs, skipped_technicians)
        
        return {
            "machine_output": machine_output,
            "human_message": human_message
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "machine_output": {
                    "success": False,
                    "error": str(e),
                    "assigned_jobs": [],
                    "failed_jobs": [],
                    "deferred_jobs": [],
                    "summary": {"total_jobs": 0, "assigned": 0, "failed": 0, "deferred": 0}
                },
                "human_message": f"❌ 시스템 오류: {str(e)}"
            }
        )


def load_technicians_from_sheets() -> bool:
    """Google Sheets에서 기사 목록 로드"""
    global _technicians_storage, _technicians_loaded
    
    try:
        sheets_client = GoogleSheetsClient()
        technicians = sheets_client.read_technicians(range_name="기사!A1:H500")
        
        with _technicians_lock:
            _technicians_storage = technicians
            _technicians_loaded = True
        
        print(f"Technicians loaded: {len(technicians)}")
        return True
        
    except Exception as e:
        print(f"기사 목록 로드 실패: {str(e)}")
        return False


def periodic_refresh():
    """주기적으로 기사 목록 갱신 (백그라운드 스레드)"""
    refresh_interval = int(os.environ.get("TECHNICIANS_REFRESH_INTERVAL", 600))
    
    while True:
        time.sleep(refresh_interval)
        print("기사 목록 주기적 갱신 시작...")
        load_technicians_from_sheets()


@app.post('/refresh-technicians')
def refresh_technicians():
    """기사 목록 수동 갱신 엔드포인트"""
    success = load_technicians_from_sheets()
    
    if success:
        return {
            "status": "ok",
            "count": len(_technicians_storage)
        }
    else:
        raise HTTPException(status_code=500, detail="기사 목록 갱신 실패")


def initialize_server():
    """
    서버 초기화 함수
    - 기사 목록 로드
    - 주기적 갱신 스레드 시작
    """
    print("서버 시작: 기사 목록 로드 중...")
    load_technicians_from_sheets()
    
    refresh_thread = threading.Thread(target=periodic_refresh, daemon=True)
    refresh_thread.start()
    print("주기적 갱신 스레드 시작됨")


# 서버 시작 시 초기화
@app.on_event("startup")
async def startup_event():
    """FastAPI startup 이벤트"""
    initialize_server()


if __name__ == "__main__":
    import uvicorn
    initialize_server()
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)
