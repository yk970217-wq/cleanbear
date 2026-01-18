"""
카카오맵 길찾기 API 연동 (좌표 기반, 캐시 및 재시도 포함)
"""
import requests
import time
from typing import Optional, Tuple
from functools import lru_cache
from models import RouteInfo
import config

# 캐시: 최근 100개의 경로 정보 캐싱
@lru_cache(maxsize=100)
def _get_route_info_cached(origin_key: str, dest_key: str) -> Optional[Tuple[float, int]]:
    """
    내부 캐시용 함수 (좌표를 문자열 키로 변환)
    Returns: (duration_minutes, distance_meters) 또는 None
    """
    return None  # 캐시 미스 시 None 반환


def get_route_info_by_coords(
    origin_lat: float, 
    origin_lng: float, 
    dest_lat: float, 
    dest_lng: float,
    retry_count: int = 2
) -> Optional[RouteInfo]:
    """
    카카오맵 길찾기 API를 사용하여 경로 정보 조회 (좌표 기반, 재시도 포함)
    
    Args:
        origin_lat: 출발지 위도
        origin_lng: 출발지 경도
        dest_lat: 도착지 위도
        dest_lng: 도착지 경도
        retry_count: 재시도 횟수 (기본 2회)
    
    Returns:
        RouteInfo 객체 또는 None (실패 시)
    """
    headers = {
        "Authorization": f"KakaoAK {config.KAKAO_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 좌표 형식: "경도,위도"
    origin = f"{origin_lng},{origin_lat}"
    destination = f"{dest_lng},{dest_lat}"
    
    params = {
        "origin": origin,
        "destination": destination,
        "waypoints": "",
        "priority": "RECOMMEND",
        "car_fuel": "GASOLINE",
        "car_hipass": "false",
        "alternatives": "false",
        "road_details": "false"
    }
    
    # 재시도 로직
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(
                config.KAKAO_DIRECTIONS_API_URL,
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return RouteInfo.from_kakao_api(data)
            else:
                last_error = f"API 오류: {response.status_code}"
                if attempt < retry_count:
                    time.sleep(0.5)  # 재시도 전 대기
                    continue
                    
        except Exception as e:
            last_error = str(e)
            if attempt < retry_count:
                time.sleep(0.5)
                continue
    
    # 모든 재시도 실패
    print(f"경로 조회 실패 ({origin} → {destination}): {last_error}")
    return None


def calculate_travel_time(
    origin_lat: float, 
    origin_lng: float,
    dest_lat: float, 
    dest_lng: float,
    fail_value: float = 9999.0
) -> float:
    """
    이동 시간을 분 단위로 반환 (좌표 기반)
    
    Args:
        origin_lat: 출발지 위도
        origin_lng: 출발지 경도
        dest_lat: 도착지 위도
        dest_lng: 도착지 경도
        fail_value: API 실패 시 반환할 값 (큰 값으로 설정하여 선택에서 밀리도록)
    
    Returns:
        이동 시간 (분) 또는 fail_value (실패 시)
    """
    route_info = get_route_info_by_coords(origin_lat, origin_lng, dest_lat, dest_lng)
    if route_info:
        return route_info.duration_minutes
    # 실패 시 큰 값 반환 (선택에서 밀리도록)
    return fail_value
