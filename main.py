"""
청소 기사 자동 배정 시스템 - HTTP API 서버

Make에서 HTTP 요청으로 호출되는 엔트리 포인트
JSON 입력을 받아서 배정 결과를 JSON으로 출력
"""
import json
from flask import Flask, request, jsonify
from datetime import date
from typing import List, Dict, Any, Tuple
from models import Job, Technician, TechnicianState, SystemRules, Assignment
from scheduler import Scheduler

app = Flask(__name__)


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
        required_fields = ["job_id", "service_type", "lat", "lng", "date", "duration_min"]
        missing_fields = [f for f in required_fields if job_data.get(f) is None]
        
        if missing_fields:
            # 필수 필드 누락 시 실패 Job 생성
            try:
                job_date = date.fromisoformat(job_data.get("date", "2000-01-01"))
            except:
                job_date = date.today()
            
            job = Job(
                job_id=job_data.get("job_id", "UNKNOWN"),
                service_type=job_data.get("service_type", ""),
                lat=job_data.get("lat", 0.0),
                lng=job_data.get("lng", 0.0),
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
                lat=job_data["lat"],
                lng=job_data["lng"],
                date=date.today(),
                duration_min=job_data["duration_min"],
                error_reason="날짜 형식 오류"
            )
            jobs.append(job)
            continue
        
        # time_fixed 검증: time_fixed=true인데 fixed_start_time 없으면 실패
        time_fixed = job_data.get("time_fixed") if job_data.get("time_fixed") is not None else None
        fixed_start_time = job_data.get("fixed_start_time") or None
        
        if time_fixed is True and (not fixed_start_time or fixed_start_time.strip() == ""):
            job = Job(
                job_id=job_data["job_id"],
                service_type=job_data["service_type"],
                lat=float(job_data["lat"]),
                lng=float(job_data["lng"]),
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
            lat=float(job_data["lat"]),
            lng=float(job_data["lng"]),
            date=job_date,
            duration_min=int(job_data["duration_min"]),
            time_fixed=time_fixed,
            fixed_start_time=fixed_start_time,  # HH:MM 형식
            slot_type=job_data.get("slot_type")  # MORNING | AFTERNOON | ALLDAY
        )
        jobs.append(job)
    
    # technicians 파싱
    for tech_data in json_data.get("technicians", []):
        # 필수 필드 체크
        required_fields = ["technician_id", "home_lat", "home_lng", "service_types", "overtime_allowed"]
        missing_fields = [f for f in required_fields if tech_data.get(f) is None]
        
        if missing_fields:
            # 필수 필드 누락 시 skipped_technicians에 기록
            skipped_technicians.append({
                "technician_id": tech_data.get("technician_id", "UNKNOWN"),
                "reason": f"필수 필드 누락: {', '.join(missing_fields)}",
                "missing_fields": missing_fields
            })
            continue
        
        technician = Technician(
            technician_id=tech_data["technician_id"],
            home_lat=float(tech_data["home_lat"]),
            home_lng=float(tech_data["home_lng"]),
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
            last_lat=float(state_data["last_lat"]) if state_data.get("last_lat") is not None else None,
            last_lng=float(state_data["last_lng"]) if state_data.get("last_lng") is not None else None,
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
    """
    기계가 읽을 배정 결과 (Make 연동용)
    """
    assigned_dicts = [a.to_dict() for a in assigned_jobs]
    failed_dicts = [a.to_dict() for a in failed_jobs]
    deferred_dicts = [a.to_dict() for a in deferred_jobs]
    
    # 요약 통계
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
    """
    사람이 읽을 메시지 생성
    
    Make에서 알림, 시트 메모, 로그로 사용
    """
    total = len(assigned_jobs) + len(failed_jobs) + len(deferred_jobs)
    if total == 0:
        return "배정할 작업이 없습니다."
    
    messages = []
    messages.append(f"📋 배정 결과 요약")
    messages.append(f"- 전체 작업: {total}건")
    messages.append(f"- 배정 완료: {len(assigned_jobs)}건")
    messages.append(f"- 배정 실패: {len(failed_jobs)}건")
    messages.append(f"- 3일 제한 초과: {len(deferred_jobs)}건")
    
    # 실패한 작업이 있으면 상세 정보 추가
    if failed_jobs:
        messages.append("")
        messages.append("⚠️ 배정 실패 작업:")
        for assignment in failed_jobs[:5]:  # 최대 5개만 표시
            reason = assignment.job.error_reason or assignment.memo or "배정 실패"
            messages.append(f"  • {assignment.job.job_id}: {reason}")
        
        if len(failed_jobs) > 5:
            messages.append(f"  ... 외 {len(failed_jobs) - 5}건")
    
    # 3일 제한 초과 작업 안내
    if deferred_jobs:
        messages.append("")
        messages.append("⏰ 3일 제한 초과 작업 (다음 배정 단계에서 처리):")
        for assignment in deferred_jobs[:3]:  # 최대 3개만 표시
            messages.append(f"  • {assignment.job.job_id}: {assignment.job.date}")
        
        if len(deferred_jobs) > 3:
            messages.append(f"  ... 외 {len(deferred_jobs) - 3}건")
    
    # 스킵된 기사가 있으면 안내
    if skipped_technicians:
        messages.append("")
        messages.append(f"⚠️ 기사 스킵: {len(skipped_technicians)}명 (필수 필드 누락)")
        for skipped in skipped_technicians[:3]:  # 최대 3개만 표시
            messages.append(f"  • {skipped['technician_id']}: {skipped['reason']}")
        
        if len(skipped_technicians) > 3:
            messages.append(f"  ... 외 {len(skipped_technicians) - 3}명")
    
    # 기본값 사용된 작업이 있으면 안내 (더 눈에 띄게)
    all_jobs = assigned_jobs + failed_jobs + deferred_jobs
    fallback_used = [a for a in all_jobs if a.job.fallback_used]
    if fallback_used:
        messages.append("")
        messages.append(f"📌 중요: 기본값 사용된 작업 {len(fallback_used)}건")
        messages.append("  (duration_min 누락/0일 때 서비스별 기본값 사용)")
        messages.append("  → 상세는 결과 데이터의 fallback_details 참조")
    
    return "\n".join(messages)


@app.route('/assign', methods=['POST'])
def assign_jobs():
    """작업 배정 API 엔드포인트"""
    try:
        # JSON 요청 데이터 읽기
        input_data = request.get_json()
        
        if not input_data:
            return jsonify({
                "machine_output": {
                    "success": False,
                    "error": "요청 데이터가 없습니다",
                    "assigned_jobs": [],
                    "failed_jobs": [],
                    "deferred_jobs": [],
                    "summary": {"total_jobs": 0, "assigned": 0, "failed": 0, "deferred": 0}
                },
                "human_message": "❌ 오류: 요청 데이터가 없습니다."
            }), 400
        
        # 데이터 파싱
        jobs, technicians, skipped_technicians, technician_states, system_rules = parse_json_input(input_data)
        
        if not jobs:
            return jsonify({
                "machine_output": {
                    "success": False,
                    "error": "작업 데이터가 없습니다",
                    "assigned_jobs": [],
                    "failed_jobs": [],
                    "deferred_jobs": [],
                    "summary": {"total_jobs": 0, "assigned": 0, "failed": 0, "deferred": 0}
                },
                "human_message": "⚠️ 작업 데이터가 없습니다."
            }), 400
        
        if not technicians:
            return jsonify({
                "machine_output": {
                    "success": False,
                    "error": "기사 데이터가 없습니다",
                    "assigned_jobs": [],
                    "failed_jobs": [],
                    "deferred_jobs": [],
                    "summary": {"total_jobs": len(jobs), "assigned": 0, "failed": 0, "deferred": 0}
                },
                "human_message": f"⚠️ 기사 데이터가 없습니다. 작업 {len(jobs)}건이 배정되지 않았습니다."
            }), 400
        
        # 배정 실행
        scheduler = Scheduler(technicians, technician_states, system_rules)
        assigned_jobs, failed_jobs, deferred_jobs = scheduler.assign_jobs(jobs)
        
        # 결과 생성
        machine_output = format_machine_output(assigned_jobs, failed_jobs, deferred_jobs, skipped_technicians)
        human_message = generate_human_message(assigned_jobs, failed_jobs, deferred_jobs, skipped_technicians)
        
        return jsonify({
            "machine_output": machine_output,
            "human_message": human_message
        }), 200
        
    except Exception as e:
        # 에러 처리
        error_output = {
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
        return jsonify(error_output), 500


@app.route('/health', methods=['GET'])
def health_check():
    """헬스 체크 엔드포인트"""
    return jsonify({
        "status": "ok",
        "service": "기사 배정 시스템"
    }), 200


if __name__ == "__main__":
    # 개발 서버 실행
    app.run(host='0.0.0.0', port=5000, debug=True)
