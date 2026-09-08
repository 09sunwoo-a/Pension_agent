# Dockerfile 규격

두 개의 Dockerfile 을 항상 함께 작성한다.

| 파일 | 용도 | 네트워크 |
|---|---|---|
| `Dockerfile` | 내부망 배포 (STG / PRD) | 내부망 전용 |
| `Dockerfile.local` | 외부망 로컬 테스트 | 공개 PyPI / Docker Hub |

---

## Dockerfile (내부망 — STG / PRD)

STG 와 PRD 는 base image 와 pip index URL 만 다르다.
기본 작성은 STG 기준으로 하고, PRD 전환 시 아래 주석 처리된 줄로 교체한다.

```dockerfile
# STG
FROM cmheastggenaiacr01.azurecr.io/python:3.10
# PRD
# FROM cmheaprdgenaiacr01.azurecr.io/python:3.10

ARG ENV_FILE_PATH
ENV ENV_PATH=$ENV_FILE_PATH

WORKDIR /custom

COPY ./requirements.txt /custom/requirements.txt
COPY .env /custom/.env
# 이후 필요한 파일 추가 복사

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

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Dockerfile.local (외부망 — 로컬 테스트용)

내부망 레지스트리·Nexus 에 접근할 수 없는 환경에서 사용한다.
공개 Docker Hub 이미지와 PyPI 를 사용한다.

```dockerfile
FROM python:3.10-slim

ARG ENV_FILE_PATH
ENV ENV_PATH=$ENV_FILE_PATH

WORKDIR /custom

COPY ./requirements.txt /custom/requirements.txt
COPY .env /custom/.env
# 이후 필요한 파일 추가 복사

RUN pip install --no-cache-dir -r /custom/requirements.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 주의 사항

- `Dockerfile` 은 내부망에서만 빌드 가능 — 외부망에서 빌드 시도 시 실패함
- `Dockerfile.local` 은 로컬 테스트 전용 — CI/CD 배포에 사용하지 않음
- `.env` 파일은 반드시 존재해야 함 (없으면 `COPY` 단계에서 빌드 실패)

## base image 변경 시 — Harbor 등록 필요

`FROM` 에 지정된 base image 를 변경하는 경우, **내부 Harbor 에 이미지가 등록되어 있어야** 내부망에서 빌드 가능하다.

- STG Harbor: `cmheastggenaiacr01.azurecr.io`
- PRD Harbor: `cmheaprdgenaiacr01.azurecr.io`

위 레지스트리에 없는 이미지를 사용하면 내부망 빌드 시 `image pull failed` 로 실패한다.
base image 변경 시 플랫폼팀에 Harbor 등록을 함께 요청해야 한다.
