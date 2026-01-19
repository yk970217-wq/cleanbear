# 청소 기사 자동 배정 시스템

Make + Cursor + 카카오맵 API를 활용한 청소 기사 자동 배정 시스템입니다.

## 시스템 구조

```
Make (구글폼/구글시트)
    ↓
ChatGPT (데이터 정리)
    ↓
Cursor HTTP API (배정 로직)
    ↑
Google Sheets (기사 목록 자동 동기화)
    ↓
Make (구글시트 기록 + 구글캘린더 등록)
```

**주요 변경사항:**
- 서버 시작 시 Google Sheets에서 기사 목록 자동 로드
- 5-10분마다 기사 목록 자동 갱신
- Make는 더 이상 기사 목록을 전송하지 않음

## 주요 기능

1. **거리 계산**: 카카오맵 길찾기 API 기반 실제 주행 거리/시간 계산 (주소 기반)
2. **3일 제한 배정**: 기사당 최대 3일치만 선배정 (초과 시 deferred_jobs로 반환)
3. **서비스 매칭**: 기사별 가능한 서비스 종류만 매칭
4. **시간 처리**: 
   - 시간 지정 고객: `time_fixed=true` + `fixed_start_time` (HH:MM)로 고정 시간 배정
   - 시간 미지정 고객: "시간 미정" 상태로 배정 (전날 통화 조율)
5. **근무 시간 관리**: system_rules에서 설정한 근무 시간, 초과 근무 옵션 지원

## 설치

```bash
pip install -r requirements.txt
```

## 환경 설정

### 필수 환경변수

1. **카카오맵 API 키**
```bash
export KAKAO_API_KEY="your_api_key_here"
```

2. **Google Sheets 인증 정보** (필수)
   ```bash
   export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
   ```
   Service Account JSON 내용을 한 줄로 환경변수에 설정합니다.

3. **Google Spreadsheet ID** (필수)
```bash
export GOOGLE_SPREADSHEET_ID="your_spreadsheet_id"
```

### 선택 환경변수

- **기사 목록 갱신 주기** (초 단위, 기본값: 600초 = 10분)
```bash
export TECHNICIANS_REFRESH_INTERVAL=600
```

### Windows에서 설정

```powershell
$env:KAKAO_API_KEY="your_api_key_here"
$env:GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
$env:GOOGLE_SPREADSHEET_ID="your_spreadsheet_id"
```

## 사용법

### 로컬 개발 서버 실행

```bash
python main.py
```

기본 포트: 5000 (PORT 환경변수로 변경 가능)

### Render 배포

**Start Command:**
```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

**중요**: 
- FastAPI + uvicorn 사용
- `--host 0.0.0.0`으로 모든 인터페이스에 바인딩
- `--port $PORT`로 Render에서 자동 설정된 포트 사용

**환경변수:**
- `KAKAO_API_KEY`: 카카오맵 API 키 (필수)
- `GOOGLE_CREDENTIALS_JSON`: Google Service Account JSON 내용 (필수, 한 줄로)
- `GOOGLE_SPREADSHEET_ID`: Google Spreadsheet ID (필수)
- `TECHNICIANS_REFRESH_INTERVAL`: 기사 목록 갱신 주기(초, 기본값: 600)
- `PORT`: Render에서 자동 설정됨

### 입력 JSON 형식 (Make 데이터 계약)

```json
{
  "jobs": [
    {
      "job_id": "J001",
      "service_type": "입주청소",
      "address": "서울시 강남구 테헤란로 123",
      "date": "2024-01-15",
      "duration_min": 180,
      "time_fixed": true,
      "fixed_start_time": "10:00"
    },
    {
      "job_id": "J002",
      "service_type": "입주청소",
      "address": "서울시 강남구 역삼동 456",
      "date": "2024-01-15",
      "duration_min": 240,
      "time_fixed": false,
      "slot_type": "AFTERNOON"
    }
  ],
  "technicians": [
    {
      "technician_id": "T001",
      "home_address": "서울시 강남구 역삼동 100",
      "service_types": ["입주청소", "이사청소"],
      "overtime_allowed": true
    }
  ],
  "technician_states": [],
  "system_rules": {
    "work_start": "09:00",
    "work_end": "18:00",
    "max_preassign_days": 3,
    "default_buffer_min": 30
  }
}
```

## API 엔드포인트

### POST /jobs

단일 작업 배정 API. ChatGPT가 생성한 job 객체를 받아서 가장 적합한 기사 1명을 선택합니다.

**요청:**
```json
{
  "job": {
    "service_type": "입주청소",
    "address": "서울 종로구 ...",
    "preferred_date": "2026-02-01",
    "duration_min": 240,
    "time_fixed": false
  }
}
```

**응답:**
```json
{
  "assigned": {
    "id": "1",
    "name": "김기사",
    "phone": "010-1234",
    "area": "고양"
  }
}
```

**에러 응답:**
```json
{
  "status": "error",
  "error": "technicians not loaded"
}
```

### POST /refresh-technicians

기사 목록 수동 갱신 엔드포인트. Google Sheets에서 기사 목록을 다시 읽어옵니다.

**응답:**
```json
{
  "status": "ok",
  "count": 10
}
```

### GET /health

헬스 체크 엔드포인트. 서버 상태와 기사 목록 로드 상태를 확인합니다.

**응답:**
```json
{
  "status": "ok",
  "service": "기사 배정 시스템",
  "technicians_loaded": true,
  "technicians_count": 10
}
```

### POST /assign

작업 배정 요청

**요청 예시:**
```bash
curl -X POST http://localhost:5000/assign \
  -H "Content-Type: application/json" \
  -d @example_input.json
```

**응답 형식:**
```json
{
  "machine_output": {
    "success": true,
    "assigned_jobs": [...],
    "failed_jobs": [...],
    "deferred_jobs": [...],
    "summary": {
      "total_jobs": 10,
      "assigned": 7,
      "failed": 1,
      "deferred": 2
    }
  },
  "human_message": "📋 배정 결과 요약\n..."
}
```

#### GET /health

헬스 체크

### 출력 필드 설명

- **assigned_jobs**: 정상 배정된 작업 (배정 완료, 시간 미정 포함)
- **failed_jobs**: 배정 실패한 작업 (에러 원인 포함)
- **deferred_jobs**: 3일 제한 초과로 다음 단계에서 배정해야 할 작업

## 배정 로직

1. **날짜별 정렬**: 작업을 날짜와 시간 순으로 정렬
2. **서비스 필터링**: 기사별 가능한 서비스 종류만 필터링
3. **3일 제한 체크**: 기사당 최대 3일치만 배정 (초과 시 deferred_jobs로 반환)
4. **거리 계산**: 카카오맵 API로 실제 이동 시간 계산 (주소 기반)
5. **시간 충돌 체크**: 기존 배정과의 시간 겹침 확인
6. **초과근무 체크**: 작업이 근무 종료 시간 초과 시 `overtime_allowed` 확인
7. **최적 기사 선택**: 이동 시간이 가장 짧은 기사 선택
8. **상태 업데이트**: 기사 위치 및 작업 시간 업데이트

## 파일 구조

- `main.py`: HTTP API 서버 (Flask 기반, gunicorn으로 배포)
- `models.py`: 데이터 모델 정의 (Job, Technician, Assignment 등)
- `scheduler.py`: 배정 알고리즘 핵심 로직
- `kakao_api.py`: 카카오맵 길찾기 API 연동 (주소 기반)
- `config.py`: 시스템 설정 (API 키, 기본값 등)
- `requirements.txt`: Python 패키지 의존성
- `example_input.json`: 입력 예시 파일

## 설정

### system_rules (Make에서 전달)

- `work_start`: 근무 시작 시간 (HH:mm 형식)
- `work_end`: 근무 종료 시간 (HH:mm 형식)
- `max_preassign_days`: 기사당 최대 배정 일수 (기본: 3일)
- `default_buffer_min`: 시스템 기본 버퍼 시간 (분 단위, 기본: 30분)

### config.py 기본값

- `DEFAULT_DURATION_BY_SERVICE`: 서비스별 기본 소요시간 (duration_min 누락 시에만 사용)

## 주의사항

1. **작업 소요시간**: Make에서 입력한 `duration_min` 값을 그대로 사용합니다. 시스템에서 계산하지 않습니다.
2. **서비스 종류**: 기사별 `service_types`에 없는 서비스는 배정 실패로 처리됩니다.
3. **시간 고정 작업**: `time_fixed=true`일 때는 반드시 `fixed_start_time` (HH:MM)이 필요합니다.
4. **카카오맵 API**: API 호출 실패 시 큰 값(9999분)으로 처리되어 선택에서 밀립니다. 재시도 로직 포함.
5. **주소 기반**: 카카오맵 길찾기 API에 주소를 직접 전달하여 거리/시간을 계산합니다.
   - 도로명 주소와 지번 주소 모두 지원
   - 주소 인식 실패 시 큰 값(9999분)으로 처리하여 배정 선택에서 밀림

## 에러 처리

- 필수 필드 누락: `failed_jobs`로 반환 (에러 원인 포함)
- 3일 제한 초과: `deferred_jobs`로 반환 (`MAX_PREASSIGN_DAYS_EXCEEDED`)
- 시간 고정 누락: `FIXED_TIME_MISSING`
- 초과근무 불가: `OVERTIME_NOT_ALLOWED`
- 기사 필수 필드 누락: `skipped_technicians`에 포함 (배정에는 사용되지 않음)
