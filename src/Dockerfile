# 내부망 배포용 (STG / PRD). 빌드 컨텍스트는 이 파일이 있는 src/ 다.
#     docker build -f Dockerfile -t pension-agent .
# 외부망에서는 빌드되지 않는다 — base image 도 pip index 도 내부망 전용이다.
# 외부망 로컬 테스트는 Dockerfile.local 을 쓴다.

# STG
FROM cmheastggenaiacr01.azurecr.io/python:3.10
# PRD
# FROM cmheaprdgenaiacr01.azurecr.io/python:3.10

ARG ENV_FILE_PATH
# 실행 단계. 배포 시 Jenkins 가 serving 을 넣어 주면 llm.py 가 같은 .env 안의
# LLM_BASE_URL_SERV(…/serv/…)을 읽는다. 워크스페이스(train)와 **같은 .env** 를 쓴다.
ENV ENV_PATH=$ENV_FILE_PATH

WORKDIR /custom

COPY ./requirements.txt /custom/requirements.txt
# 워크스페이스에서 쓰던 그 .env 가 그대로 들어간다(단계별 URL 두 벌이 함께 있다).
# 없으면 여기서 빌드가 실패한다(refs/dockerfile.md).
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

# 미리 만들어 둔 브리핑(scripts/prebuild_briefings.py). 저장소에 있으므로 따로 만들 것은
# 없다 — 지우고 빌드하면 그때는 .env 처럼 COPY 단계에서 실패한다.
#
# 왜 넣나: 대화형은 브리핑이 **이미 있다고 보고** 답한다. 이 컨테이너가 직접 만들면
# 고객당 순차 LLM 11 회이고, 외부에서 호출할 때마다 그 시간을 문다. 구워 넣으면 첫
# 호출부터 읽어 쓴다.
#
# 비어 있어도 의미가 있다 — **디렉터리가 있으면 저장소가 켜지고**(briefing_store.enabled),
# 그러면 런타임에 만든 브리핑을 컨테이너가 스스로 저장한다. 날짜가 넘어가 지문이 어긋난
# 다음 날에도 고객당 첫 호출만 11 회를 치르고 그 뒤부터는 저장분을 읽는다.
# 지금 무엇이 읽히고 있는지는 `/health` 의 `briefing_cache` 가 답한다.
RUN mkdir -p /custom/briefing_cache
COPY ./briefing_cache /custom/briefing_cache

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
