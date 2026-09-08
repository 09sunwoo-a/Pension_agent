# requirements.txt 규격

## 기본 패키지 (검증된 버전)

```
fastapi==0.115.12
uvicorn==0.34.2
python-dotenv==1.1.0
langchain==0.3.23
langchain-community==0.3.21
langchain-core==0.3.58
langchain-openai==0.3.14
```

Python 버전: **3.10**

---

## 패키지 추가·변경 시 주의사항

위 버전 목록 또는 Python 3.10 을 벗어나는 경우, **내부망 Nexus 에 등록이 필요**하다.
등록 전에는 내부망에서 pip install 이 실패한다.

**플랫폼팀 등록 요청 절차:**
1. 추가/변경할 패키지 목록 확인
2. 라이선스 검사 — GPL 계열 등 사용 제한 라이선스 여부 확인
3. 취약점 검사 — `pip-audit` 또는 `safety` 로 CVE 스캔
4. 검사 결과와 함께 플랫폼팀에 Nexus 등록 요청
