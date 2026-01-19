"""
Google Sheets API 연동 모듈
"""
import os
import json
from typing import List, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from models import Technician


class GoogleSheetsClient:
    """Google Sheets 클라이언트"""
    
    def __init__(self, spreadsheet_id: Optional[str] = None):
        """
        초기화
        
        Args:
            spreadsheet_id: 스프레드시트 ID (환경변수 GOOGLE_SPREADSHEET_ID)
        """
        # 환경변수에서 설정 읽기 (GOOGLE_CREDENTIALS_JSON만 사용)
        credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        self.spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SPREADSHEET_ID")
        
        if not credentials_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 필요합니다")
        
        if not self.spreadsheet_id:
            raise ValueError("Google Spreadsheet ID가 필요합니다 (GOOGLE_SPREADSHEET_ID 환경변수)")
        
        # 인증 정보 로드
        try:
            credentials_info = json.loads(credentials_json)
            self.credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
        except json.JSONDecodeError:
            raise ValueError("GOOGLE_CREDENTIALS_JSON 환경변수가 유효한 JSON 형식이 아닙니다")
        
        # Sheets API 서비스 생성
        self.service = build('sheets', 'v4', credentials=self.credentials)
    
    def read_technicians(self, range_name: str = "기사!A1:H500") -> List[Technician]:
        """
        Google Sheets에서 기사 목록 읽기
        
        Args:
            range_name: 읽을 범위 (예: "기사!A1:H500")
        
        Returns:
            Technician 객체 리스트
        """
        try:
            # 시트 읽기
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if len(values) < 2:
                return []  # 헤더만 있거나 데이터가 없음
            
            # 헤더 파싱
            headers = values[0]
            header_map = {h.lower(): idx for idx, h in enumerate(headers)}
            
            # 필수 헤더 체크
            if "id" not in header_map:
                raise ValueError("헤더에 'id' 필드가 없습니다")
            
            technicians = []
            
            # 데이터 행 파싱
            for row in values[1:]:
                # row 길이가 header보다 짧으면 빈 문자열로 채우기
                padded_row = row + [""] * (len(headers) - len(row))
                
                tech_id = str(padded_row[header_map.get("id", 0)]).strip()
                if not tech_id:
                    continue  # ID가 없으면 스킵
                
                # 필드 추출 (인덱스 범위 체크)
                def get_field(field_name: str, default: str = "") -> str:
                    idx = header_map.get(field_name, -1)
                    if idx >= 0 and idx < len(padded_row):
                        return str(padded_row[idx]).strip()
                    return default
                
                name = get_field("name", "")
                phone = get_field("phone", "")
                area = get_field("area", "")
                
                # service_types 파싱 (쉼표로 구분)
                service_types_str = get_field("service_types", "")
                service_types = [s.strip() for s in service_types_str.split(",") if s.strip()] if service_types_str else []
                
                # priority 파싱
                priority_str = get_field("priority", "0")
                try:
                    priority = int(priority_str)
                except:
                    priority = 0
                
                # overtime_allowed 파싱
                overtime_str = get_field("overtime_allowed", "true")
                overtime_allowed = overtime_str.lower() in ("true", "1", "on", "yes", "y")
                
                # area를 home_address로 사용
                home_address = area if area else ""
                
                technician = Technician(
                    technician_id=tech_id,
                    home_address=home_address,
                    service_types=service_types,
                    overtime_allowed=overtime_allowed,
                    name=name,
                    phone=phone,
                    area=area,
                    priority=priority
                )
                technicians.append(technician)
            
            return technicians
            
        except HttpError as error:
            print(f"Google Sheets API 오류: {error}")
            raise
        except Exception as e:
            print(f"기사 목록 읽기 실패: {str(e)}")
            raise
