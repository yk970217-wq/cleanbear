"""
클린베어 에어컨청소 기사 자동 배정 엔진
단계별 구현: CP1 → CP2 → CP3
"""

import math
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# Flask는 API 엔드포인트에서만 사용하므로 조건부 import
try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# ==================== 거리 계산 ====================

def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 공식을 사용한 두 좌표 간 직선 거리 계산 (km)"""
    R = 6371  # 지구 반지름 (km)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# ==================== 서비스 시간 계산 ====================

def calc_service_min(service_type: str, units: int, servicetimes: List[Dict], tech_factors: Dict[str, float]) -> int:
    """서비스 시간 계산: base_min_per_unit * units * tech_factor"""
    # servicetimes에서 base_min_per_unit 찾기
    base_min = 120  # 기본값
    for st in servicetimes:
        if st.get("service_type") == service_type:
            base_min = st.get("base_min_per_unit", 120)
            break
    
    # tech_factors에서 해당 서비스의 factor 찾기 (factor_wall, factor_stand 등 또는 factor JSON)
    tech_factor = 1.0
    if service_type in tech_factors:
        tech_factor = tech_factors[service_type]
    else:
        # factor_wall, factor_stand 등으로도 찾기 시도
        factor_key = f"factor_{service_type}"
        if factor_key in tech_factors:
            tech_factor = tech_factors[factor_key]
    
    return int(base_min * units * tech_factor)


# ==================== TechOff 필터링 ====================

def is_tech_off(tech_id: str, preferred_date: str, techoffs: List[Dict]) -> bool:
    """기사가 해당 날짜에 휴무인지 확인"""
    for toff in techoffs:
        if toff.get("tech_id") == tech_id and toff.get("off_date") == preferred_date:
            return True
    return False


# ==================== 시간 슬롯 관리 (CP2용) ====================

def time_to_minutes(time_str: str) -> int:
    """시간 문자열(HH:MM)을 분으로 변환"""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def minutes_to_time(minutes: int) -> str:
    """분을 시간 문자열(HH:MM)로 변환"""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def get_time_band_range(time_band: str) -> Tuple[int, int]:
    """time_band에 따른 시작/종료 시간(분) 반환"""
    # 오전: 09:00-12:00, 오후: 12:00-18:00, 상관없음: 09:00-18:00
    if time_band == "오전":
        return (9 * 60, 12 * 60)  # 09:00 ~ 12:00
    elif time_band == "오후":
        return (12 * 60, 18 * 60)  # 12:00 ~ 18:00
    else:  # 상관없음
        return (9 * 60, 18 * 60)  # 09:00 ~ 18:00


def can_fit_in_time_band(start_min: int, end_min: int, time_band: str) -> bool:
    """해당 시간 슬롯이 time_band 제약을 만족하는지 확인"""
    band_start, band_end = get_time_band_range(time_band)
    return start_min >= band_start and end_min <= band_end


# ==================== 배정 엔진 ====================

class AssignmentEngine:
    def __init__(self, works: List[Dict], techs: List[Dict], techoffs: List[Dict], 
                 servicetimes: List[Dict], last_index: int = 0):
        self.works = works
        self.techs = techs
        self.techoffs = techoffs
        self.servicetimes = servicetimes
        self.last_index = last_index
        
        # 기사별 배정 상태 추적 (CP2: 시간 슬롯용)
        self.tech_schedules = defaultdict(list)  # {tech_id: [(start_min, end_min, work), ...]}
        
        # 지역 몰이용 (CP3)
        self.region_anchors = {}  # {preferred_date: (lat, lng)}
        
    def get_available_techs(self, preferred_date: str) -> List[Dict]:
        """해당 날짜에 배정 가능한 기사 목록 반환 (active=True, TechOff 제외)"""
        available = []
        for tech in self.techs:
            if not tech.get("active", False):
                continue
            if is_tech_off(tech.get("tech_id"), preferred_date, self.techoffs):
                continue
            available.append(tech)
        return available
    
    def get_tech_factors(self, tech: Dict) -> Dict[str, float]:
        """기사의 숙련도 계수를 Dict 형태로 반환"""
        factors = {}
        # factor JSON이 있는 경우
        if "factor" in tech and isinstance(tech["factor"], dict):
            factors.update(tech["factor"])
        # factor_wall, factor_stand 등 개별 컬럼이 있는 경우
        for key, value in tech.items():
            if key.startswith("factor_") and isinstance(value, (int, float)):
                service_type = key.replace("factor_", "")
                factors[service_type] = value
        return factors
    
    def get_work_location(self, work: Dict) -> Optional[Tuple[float, float]]:
        """작업의 좌표 반환 (lat, lng 또는 address geocoding 필요)"""
        if "lat" in work and "lng" in work:
            return (work["lat"], work["lng"])
        # TODO: address를 geocoding하여 lat/lng 얻기 (현재는 lat/lng 필요)
        return None
    
    def calculate_score(self, work: Dict, tech: Dict, travel_km: float, 
                       service_min: int, load_penalty: int = 0) -> float:
        """배정 점수 계산: 낮을수록 좋음"""
        # 기본 점수식: travel_km*10 + service_min*0.3 + load_penalty
        score = travel_km * 10 + service_min * 0.3 + load_penalty
        
        # CP3: 지역 몰이 - anchor 근처면 보너스 (점수 감소)
        preferred_date = work.get("preferred_date")
        if preferred_date in self.region_anchors:
            anchor_lat, anchor_lng = self.region_anchors[preferred_date]
            tech_lat = tech.get("home_lat") or tech.get("lat")
            tech_lng = tech.get("home_lng") or tech.get("lng")
            anchor_dist = distance_km(work.get("lat"), work.get("lng"), anchor_lat, anchor_lng)
            if anchor_dist < 5.0:  # 5km 이내면 보너스
                score -= anchor_dist * 2  # 추가 보너스
        
        return score
    
    def find_best_tech_cp1(self, work: Dict) -> Optional[Dict]:
        """CP1: 기본 배정 - 거리와 작업시간 기반"""
        preferred_date = work.get("preferred_date")
        available_techs = self.get_available_techs(preferred_date)
        
        if not available_techs:
            return None
        
        work_loc = self.get_work_location(work)
        if not work_loc:
            return None
        
        work_lat, work_lng = work_loc
        
        best_tech = None
        best_score = float('inf')
        best_result = None
        
        # 기사별 배정 건수 계산 (load_penalty용)
        tech_assign_counts = defaultdict(int)
        for r in getattr(self, '_results', []):
            if 'assigned_tech_id' in r:
                tech_assign_counts[r['assigned_tech_id']] += 1
        
        # last_index 기반 로테이션 적용
        if available_techs:
            start_idx = self.last_index % len(available_techs)
            available_techs = available_techs[start_idx:] + available_techs[:start_idx]
        
        for tech in available_techs:
            tech_id = tech.get("tech_id")
            
            # 거리 계산
            tech_lat = tech.get("home_lat") or tech.get("lat")
            tech_lng = tech.get("home_lng") or tech.get("lng")
            travel_km = distance_km(work_lat, work_lng, tech_lat, tech_lng)
            
            # 작업 시간 계산
            tech_factors = self.get_tech_factors(tech)
            service_min = calc_service_min(
                work.get("service_type", ""),
                work.get("units", 1),
                self.servicetimes,
                tech_factors
            )
            
            # load_penalty (건수 * 5)
            load_penalty = tech_assign_counts.get(tech_id, 0) * 5
            
            # 점수 계산
            score = self.calculate_score(work, tech, travel_km, service_min, load_penalty)
            
            if score < best_score:
                best_score = score
                best_tech = tech
                best_result = {
                    "tech_id": tech_id,
                    "travel_km": round(travel_km, 2),
                    "service_min": service_min
                }
        
        return best_result
    
    def find_best_tech_cp2(self, work: Dict) -> Optional[Dict]:
        """CP2: time_band 제약을 포함한 배정"""
        preferred_date = work.get("preferred_date")
        time_band = work.get("time_band", "상관없음")
        available_techs = self.get_available_techs(preferred_date)
        
        if not available_techs:
            return None
        
        work_loc = self.get_work_location(work)
        if not work_loc:
            return None
        
        work_lat, work_lng = work_loc
        
        # 작업 시간 계산을 위해 service_min 미리 계산
        service_type = work.get("service_type", "")
        units = work.get("units", 1)
        
        best_tech = None
        best_score = float('inf')
        best_result = None
        
        # 기사별 배정 건수 계산
        tech_assign_counts = defaultdict(int)
        for r in getattr(self, '_results', []):
            if 'assigned_tech_id' in r:
                tech_assign_counts[r['assigned_tech_id']] += 1
        
        # last_index 기반 로테이션
        if available_techs:
            start_idx = self.last_index % len(available_techs)
            available_techs = available_techs[start_idx:] + available_techs[:start_idx]
        
        for tech in available_techs:
            tech_id = tech.get("tech_id")
            
            # 거리 계산
            tech_lat = tech.get("home_lat") or tech.get("lat")
            tech_lng = tech.get("home_lng") or tech.get("lng")
            travel_km = distance_km(work_lat, work_lng, tech_lat, tech_lng)
            
            # 작업 시간 계산
            tech_factors = self.get_tech_factors(tech)
            service_min = calc_service_min(service_type, units, self.servicetimes, tech_factors)
            
            # 해당 기사의 현재 일정 확인하여 가능한 시간 슬롯 찾기
            tech_schedule = sorted(self.tech_schedules.get(tech_id, []))
            band_start, band_end = get_time_band_range(time_band)
            
            # 기사 일정에서 빈 시간 찾기
            slot_start = band_start
            slot_found = False
            
            # 기사 일정이 없으면 바로 배정 가능
            if not tech_schedule:
                slot_start = band_start
                slot_found = True
            else:
                # 기사 일정 사이의 빈 시간 확인
                for i, (start_min, end_min, _) in enumerate(tech_schedule):
                    if slot_start + service_min <= start_min and slot_start + service_min <= band_end:
                        slot_found = True
                        break
                    slot_start = max(slot_start, end_min)
                
                # 마지막 작업 이후에도 시간이 있으면
                if not slot_found and slot_start + service_min <= band_end:
                    slot_found = True
            
            if not slot_found:
                continue  # 이 기사는 시간 제약을 만족하지 않음
            
            slot_end = slot_start + service_min
            
            # load_penalty
            load_penalty = tech_assign_counts.get(tech_id, 0) * 5
            
            # 점수 계산
            score = self.calculate_score(work, tech, travel_km, service_min, load_penalty)
            
            if score < best_score:
                best_score = score
                best_tech = tech
                best_result = {
                    "tech_id": tech_id,
                    "travel_km": round(travel_km, 2),
                    "service_min": service_min,
                    "start_time": minutes_to_time(slot_start),
                    "end_time": minutes_to_time(slot_end)
                }
        
        # 배정이 결정되면 해당 기사의 일정에 추가
        if best_result:
            tech_id = best_result["tech_id"]
            start_min = time_to_minutes(best_result["start_time"])
            end_min = time_to_minutes(best_result["end_time"])
            self.tech_schedules[tech_id].append((start_min, end_min, work))
            self.tech_schedules[tech_id].sort()  # 시간순 정렬
        
        return best_result
    
    def assign(self, cp_level: int = 1) -> Tuple[List[Dict], int]:
        """
        배정 실행
        cp_level: 1=CP1 (기본), 2=CP2 (시간제약), 3=CP3 (지역몰이)
        """
        self._results = []
        new_last_index = self.last_index
        
        # CP3: 지역 몰이 - 첫 작업의 좌표를 anchor로 설정
        if cp_level >= 3 and self.works:
            first_work = self.works[0]
            preferred_date = first_work.get("preferred_date")
            work_loc = self.get_work_location(first_work)
            if work_loc and preferred_date:
                self.region_anchors[preferred_date] = work_loc
        
        # 작업 순서대로 배정
        for work in self.works:
            row_number = work.get("rowNumber")
            
            if cp_level >= 2:
                best = self.find_best_tech_cp2(work)
            else:
                best = self.find_best_tech_cp1(work)
            
            if best:
                result = {
                    "rowNumber": row_number,
                    "assigned_tech_id": best["tech_id"],
                    "assign_status": "ASSIGNED",
                    "travel_km": best["travel_km"],
                    "calc_service_min": best["service_min"]
                }
                
                if "start_time" in best:
                    result["start_time"] = best["start_time"]
                if "end_time" in best:
                    result["end_time"] = best["end_time"]
                
                # last_index 업데이트 (배정된 기사의 인덱스)
                available_techs = self.get_available_techs(work.get("preferred_date", ""))
                for idx, tech in enumerate(available_techs):
                    if tech.get("tech_id") == best["tech_id"]:
                        new_last_index = idx
                        break
            else:
                result = {
                    "rowNumber": row_number,
                    "assign_status": "NEEDS_HELP"
                }
            
            self._results.append(result)
        
        return self._results, new_last_index


# ==================== API 엔드포인트 (Flask) ====================

if FLASK_AVAILABLE:
    app = Flask(__name__)
else:
    app = None

if FLASK_AVAILABLE:
    @app.route('/assign', methods=['POST'])
    def assign_endpoint():
        """배정 API 엔드포인트"""
        try:
            data = request.json
            
            works = data.get("works", [])
            techs = data.get("techs", [])
            techoffs = data.get("techoffs", [])
            servicetimes = data.get("servicetimes", [])
            last_index = data.get("system", {}).get("last_index", 0)
            cp_level = data.get("cp_level", 1)  # 기본 CP1
            
            engine = AssignmentEngine(works, techs, techoffs, servicetimes, last_index)
            results, new_last_index = engine.assign(cp_level=cp_level)
            
            return jsonify({
                "success": True,
                "results": results,
                "new_last_index": new_last_index
            })
        
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 400


    @app.route('/health', methods=['GET'])
    def health():
        """헬스 체크 엔드포인트"""
        return jsonify({"status": "ok"})

else:
    # Flask가 없을 때 더미 app
    app = None


if __name__ == "__main__":
    # 로컬 테스트용
    if len(__import__('sys').argv) > 1 and __import__('sys').argv[1] == 'test':
        # 테스트 코드
        works = [{
            "rowNumber": 12,
            "lat": 37.570,
            "lng": 126.982,
            "service_type": "벽걸이",
            "units": 1,
            "preferred_date": "2026-01-20",
            "time_band": "오전"
        }]
        
        techs = [{
            "tech_id": "T1",
            "home_lat": 37.571,
            "home_lng": 126.990,
            "active": True,
            "factor_wall": 0.9
        }]
        
        techoffs = []
        servicetimes = [{
            "service_type": "벽걸이",
            "base_min_per_unit": 90
        }]
        
        engine = AssignmentEngine(works, techs, techoffs, servicetimes, 0)
        results, new_idx = engine.assign(cp_level=1)
        print("CP1 결과:", results)
        
        engine2 = AssignmentEngine(works, techs, techoffs, servicetimes, 0)
        results2, new_idx2 = engine2.assign(cp_level=2)
        print("CP2 결과:", results2)
    else:
        # API 서버 실행
        if FLASK_AVAILABLE and app:
            port = int(os.environ.get('PORT', 5000))
            app.run(host='0.0.0.0', port=port, debug=False)
        else:
            print("Flask가 설치되지 않았습니다. 'pip install flask' 실행 후 다시 시도하세요.")
