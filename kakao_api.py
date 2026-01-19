"""
카카오맵 길찾기 API 연동 (주소 기반, 캐시 및 재시도 포함)
"""
import requests
import time
from typing import Optional
from models import RouteInfo
import config


def get_route_info_by_address(
    origin_address: str,
    dest_address: str,
    retry_count: int = 2
) -> Optional[RouteInfo]:
    """
    카카오맵 길찾기 API를 사용하여 경로 정보 조회 (주소 기반, 재시도 포함)
    
    카카오맵 API는 도로명 주소와 지번 주소 모두를 지원합니다.
    예: 
    - 도로명: "서울시 강남구 테헤란로 123"
    - 지번: "서울시 강남구 역삼동 456"
    
    Args:
        origin_address: 출발지 주소 (도로명 또는 지번 주소)
        dest_address: 도착지 주소 (도로명 또는 지번 주소)
        retry_count: 재시도 횟수 (기본 2회)
    
    Returns:
        RouteInfo 객체 또는 None (실패 시: 주소 인식 실패, API 오류 등)
    """
    headers = {
        "Authorization": f"KakaoAK {config.KAKAO_API_KEY}",
        "Content-Type": "application/json"
    }
    
    params = {
        "origin": origin_address,
        "destination": dest_address,
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
                # 응답 데이터 검증
                routes = data.get("routes", [])
                if not routes:
                    # 주소를 인식하지 못한 경우 (도로명/지번 주소 오류 가능)
                    last_error = "주소를 인식하지 못했습니다. 도로명 주소 또는 지번 주소를 확인해주세요."
                    if attempt < retry_count:
                        time.sleep(0.5)
                        continue
                    break
                return RouteInfo.from_kakao_api(data)
            elif response.status_code == 400:
                # 잘못된 주소 형식 (400 Bad Request)
                try:
                    error_data = response.json()
                    error_msg = error_data.get("msg", "주소 형식 오류")
                except:
                    error_msg = "주소 형식 오류"
                last_error = f"주소 형식 오류 (400): {error_msg}"
                # 400 에러는 재시도해도 의미 없으므로 즉시 종료
                break
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
    print(f"경로 조회 실패 ({origin_address} → {dest_address}): {last_error}")
    return None


def calculate_travel_time(
    origin_address: str,
    dest_address: str,
    fail_value: float = 9999.0
) -> float:
    """
    이동 시간을 분 단위로 반환 (주소 기반)
    
    카카오맵 API가 도로명 주소/지번 주소를 인식하지 못하거나 
    API 오류 발생 시 fail_value를 반환하여 배정 선택에서 밀리도록 처리합니다.
    
    Args:
        origin_address: 출발지 주소 (도로명 또는 지번 주소)
        dest_address: 도착지 주소 (도로명 또는 지번 주소)
        fail_value: API 실패 시 반환할 값 (기본 9999.0분)
    
    Returns:
        이동 시간 (분) 또는 fail_value (실패 시)
    """
    # 주소 검증 (빈 문자열 체크)
    if not origin_address or not origin_address.strip():
        print(f"경고: 출발지 주소가 비어있습니다: {origin_address}")
        return fail_value
    
    if not dest_address or not dest_address.strip():
        print(f"경고: 도착지 주소가 비어있습니다: {dest_address}")
        return fail_value
    
    route_info = get_route_info_by_address(origin_address, dest_address)
    if route_info:
        return route_info.duration_minutes
    # 실패 시 큰 값 반환 (선택에서 밀리도록)
    return fail_value
