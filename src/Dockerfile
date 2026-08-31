# 내부망 배포용 (STG / PRD). 빌드 컨텍스트는 이 파일이 있는 src/ 다.
#     docker build -f Dockerfile -t pension-agent .
# 외부망에서는 빌드되지 않는다 — base image 도 pip index 도 내부망 전용이다.
# 외부망 로컬 테스트는 Dockerfile.local 을 쓴다.

# STG
FROM cmheastggenaiacr01.azurecr.io/python:3.10
# PRD
# FROM cmheaprdgenaiacr01.azurecr.io/python:3.10

ARG ENV_FILE_PATH
ENV ENV_PATH=$ENV_FILE_PATH

WORKDIR /custom

COPY ./requirements.txt /custom/requirements.txt
COPY .env /custom/.env

# STG
RUN pip install --no-cache-dir \
    --index-url https://stg-nexus-genaihub.kbonecloud.com/repository/pypi/simple \
    --trusted-host stg-nexus-genaihub.kbonecloud.com \
    -r /custom/requirements.txt
# PRD
# RUN pip install --no-cache-dir \
#     --index-url https://nexus-genaihub.kbonecloud.com/repository/pypi/simple \
#     --trusted-host nexus-genaihub.kbonecloud.com \
#     -r /custom/requirements.txt

# 런타임에 필요한 것만 담는다.
#   main.py        HTTP 진입점
#   pension_agent/ 에이전트 본체 + 지식 카드·전략 카탈로그·고객 원장(생성된 JSON)
#   session_data/  상담이력(history 도구가 되읽는다)
# 저장소 루트의 01~09 지식베이스 «원문» 폴더는 넣지 않는다 — 카드 생성기(scripts/kb_build)
# 만 읽고 런타임은 산출된 JSON 만 본다(config.REPO_ROOT 는 런타임 코드가 쓰지 않는다).
# app.py·tests/·scripts/ 도 넣지 않는다 — 개발용이고 streamlit 의존성을 끌고 온다.
COPY ./main.py /custom/main.py
COPY ./pension_agent /custom/pension_agent
COPY ./session_data /custom/session_data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
