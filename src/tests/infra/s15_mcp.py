"""행내 MCP 연동(pension_agent/common/mcp) — 쪽지 발송 층.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

import asyncio
import threading as _threading
import time as _time

from tests.infra._common import check

import os
from tests.infra.s14_note_body import _note, note   # 앞 구간에서 이어받음


# 행내 패키지(mcp_sdk · langchain_mcp_adapters)는 가짜로 대체(`client.use_backend`)
# 측정 대상: 우리가 내보내는 것 — 인증 헤더 꼴 · 도구·인자 이름 · 재시도 정책 · 미연결 처리

import base64 as _b64  # noqa: E402
import hashlib as _hl  # noqa: E402
import hmac as _hm  # noqa: E402
import json as _js  # noqa: E402

from pension_agent.common import mcp as _mcp  # noqa: E402
from pension_agent.common.mcp import client as _mcpc  # noqa: E402
from pension_agent.common.mcp import servers as _mcps  # noqa: E402
from pension_agent.common.mcp import workb as _mcpw  # noqa: E402

_MCP_ENV = ("MCP_SERVER_URL", "MCP_USER_ID", "MCP_SECRET_KEY", "MCP_CONN_ID", "MCP_SERVERS",
            "MCP_RETRY_ATTEMPTS", "MCP_RETRY_BACKOFF_SEC", "MCP_AUTH_TTL_SEC", note.EMP_NO_ENV)
_saved_mcp_env = {k: os.environ.get(k) for k in _MCP_ENV}
_saved_issue_token, _saved_sender = _mcpc.issue_token, note.SENDER


class _FakeTool:
    """도구 대역. `fail` 번째 호출까지 실패(재시도 정책 측정용)."""

    def __init__(self, name: str, fail: int = 0, answer: str = '{"success": true}'):
        self.name, self.fail, self.answer = name, fail, answer
        self.calls: list[dict] = []

    async def ainvoke(self, args):
        self.calls.append(args)
        if len(self.calls) <= self.fail:
            raise TimeoutError("전송 끊김")
        return [{"type": "text", "text": self.answer}]


class _FakeSdk:
    """행내 인증 SDK 대역 — 호출 순서만 기록."""

    log: list[tuple] = []

    @staticmethod
    def install():
        _FakeSdk.log.append(("install",))

    @staticmethod
    def setup_system(client_id, client_secret):
        _FakeSdk.log.append(("setup_system", client_id))

    #: SDK 전역의 현재 사번 — 실제 SDK 처럼 마지막 설정값 유지
    current: str = ""

    @staticmethod
    def set_request_context(emp_no, auth_token):
        _FakeSdk.log.append(("context", emp_no, auth_token))
        _FakeSdk.current = emp_no


def _fake_adapter(tools):
    """`MultiServerMCPClient` 대역 — 받은 서버 목록 보관."""

    class _Adapter:
        made: list[dict] = []

        def __init__(self, server_list):
            _Adapter.made.append(server_list)

        async def get_tools(self):
            return list(tools)

    return _Adapter


def _use_mcp(*, tools, retry: str = "2", servers_env: str = "workb"):
    """가짜 백엔드로 교체, 접속 재생성."""
    os.environ.update({"MCP_SERVER_URL": "https://mcp.test/api", "MCP_USER_ID": "tea000",
                       "MCP_SECRET_KEY": "s3cret", "MCP_SERVERS": servers_env,
                       "MCP_RETRY_ATTEMPTS": retry, "MCP_RETRY_BACKOFF_SEC": "0",
                       note.EMP_NO_ENV: "3902172"})
    adapter = _fake_adapter(tools)
    _FakeSdk.log.clear()
    _mcp.reset()
    _mcpc.use_backend(sdk=_FakeSdk, adapter=adapter)
    _mcpc.issue_token = lambda cfg: "jwt-test"
    return adapter


try:
    # 설정 없으면 미연결 — 쪽지는 «미연결» 응답
    for _k in _MCP_ENV:
        os.environ.pop(_k, None)
    note.use_sender(None)
    check(_mcp.install() is False and note.SENDER is None,
          "mcp.install: 설정이 없으면 붙이지 않는다 — 쪽지는 «미연결»로 답한다")
    check(_mcp.stats()["missing"] == ["MCP_SERVER_URL", "MCP_USER_ID", "MCP_SECRET_KEY"],
          "mcp.stats: 무엇이 비어 있는지 이름으로 말한다", str(_mcp.stats()))

    # 연결 시: 인증 헤더 · 주소 · 도구 인자
    _memo_tool = _FakeTool("send_memo")
    _name_tool = _FakeTool("search_emp_and_send_memo")
    _adapter = _use_mcp(tools=[_memo_tool, _name_tool])
    check(_mcp.install() is True and note.SENDER is _mcpw.send_memo
          and note.NAME_SENDER is _mcpw.search_emp_and_send_memo,
          "mcp.install: 설정이 갖춰지면 위층(note)의 발송 함수로 등록된다(사번 · 이름)")

    _sent = note.send_note_sync(["3902172"], _note)
    check(_sent["status"] == "sent", "mcp: 승낙받은 쪽지가 MCP 도구로 나가고 발송으로 판정된다",
          str(_sent))
    check(_memo_tool.calls == [{"RECIPIENT": ["3902172"], "TITLE": _note.title,
                                "BODY": _note.body}],
          "mcp.workb: 수신자·제목·본문이 WorkB 규격의 인자 이름으로 나간다",
          str(_memo_tool.calls))

    _spec = _adapter.made[0]["workb-mcp-server"]
    check(_spec["url"] == "https://mcp.test/api/workb/tea000" and _spec["transport"] == "sse",
          "mcp.servers: 게이트웨이 주소는 «{base}/workb/{client_id}»", str(_spec["url"]))
    check(_spec["headers"]["Authorization"] == "Bearer jwt-test",
          "mcp: 발급받은 토큰이 Authorization 헤더로 간다")

    # MCP-User-Key 서명 — 규격 불일치 시 서버 사유가 «기타 오류»뿐
    _key = _js.loads(_b64.b64decode(_spec["headers"]["MCP-User-Key"]).decode())
    _msg = f"{_key['timestamp']}{_key['request_id']}{_key['emp_no']}".encode()
    _sig = _b64.b64encode(_hm.new(b"s3cret", _msg, _hl.sha256).digest()).decode()
    check(_key["emp_no"] == "3902172" and _key["client_id"] == "tea000",
          "mcp.user_key: 사번과 클라이언트 id 가 그대로 실린다", str(_key.get("emp_no")))
    check(_key["signature"] == _sig,
          "mcp.user_key: 서명은 (시각+요청id+사번)의 HMAC-SHA256 이다")

    # 로그인 사번으로 인증(감사 기록), 받는 사람과는 별개 축
    note.send_note_sync(["3902172"], _note, as_employee="3902174")
    _key2 = _js.loads(_b64.b64decode(
        _adapter.made[-1]["workb-mcp-server"]["headers"]["MCP-User-Key"]).decode())
    check(_key2["emp_no"] == "3902174" and _memo_tool.calls[-1]["RECIPIENT"] == ["3902172"],
          "mcp: 로그인 사번이 «보내는 사람»으로 인증에 실린다(받는 사람과 다른 축)",
          f"{_key2['emp_no']} → {_memo_tool.calls[-1]['RECIPIENT']}")
    check(sorted(_mcp.stats()["connected"]) == ["3902172", "3902174"],
          "mcp: 사번마다 접속이 따로 열린다", str(_mcp.stats()["connected"]))
    check(_mcpc.user_key("tea000", "s3cret", "3902172")
          != _mcpc.user_key("tea000", "s3cret", "3902172"),
          "mcp.user_key: 같은 사번이어도 요청마다 다른 값이다(요청 id·시각)")

    # 요청 컨텍스트는 프로세스 전역 — 호출 직전에 재설정(다른 직원 요청 끼어듦 방지)
    check(_FakeSdk.log[:2] == [("install",), ("setup_system", "tea000")],
          "mcp: SDK 활성화·인증 정보 등록은 접속 전에 한 번뿐이다", str(_FakeSdk.log[:2]))
    check(_FakeSdk.log.count(("context", "3902172", "jwt-test")) == 2,
          "mcp: 요청 컨텍스트를 접속할 때와 **호출 직전에** 다시 세운다", str(_FakeSdk.log))

    # 이름 발송 — 위 호출 횟수 검사 뒤에 위치
    note.send_note_by_name_sync("김국민", "미아동지점", _note)
    check(_name_tool.calls[-1:] == [{"user_name": "김국민", "title": _note.title,
                                     "body": _note.body, "group_name": "미아동지점"}],
          "mcp.workb: 이름 발송은 소문자 user_name·group_name·title·body 로 나간다"
          "(대문자 TITLE·BODY 는 서버가 «Missing required parameters» 로 거부했다)",
          str(_name_tool.calls))
    # 도구가 밝힌 인자 이름의 대소문자 준수
    _name_tool.args = {"USER_NAME": {}, "TITLE": {}, "BODY": {}, "GROUP_NAME": {}}
    note.send_note_by_name_sync("김국민", None, _note)
    check(_name_tool.calls[-1] == {"USER_NAME": "김국민", "TITLE": _note.title, "BODY": _note.body},
          "mcp.workb: 이름 발송 인자는 도구가 밝힌 이름의 대소문자를 따른다", str(_name_tool.calls[-1]))

    # 발송은 재시도 금지 — 타임아웃 = «나갔는지 모름», 재시도 = 중복 발송
    _flaky = _FakeTool("send_memo", fail=99)
    _use_mcp(tools=[_flaky])
    _mcp.install()
    _failed = note.send_note_sync(["3902172"], _note)
    check(_failed["status"] == "failed" and len(_flaky.calls) == 1,
          "mcp: 발송이 실패해도 다시 부르지 않는다 — 재시도가 곧 중복 발송이다",
          f"{_failed['status']} · 호출 {len(_flaky.calls)}회")

    # 실패 문구(로그·화면)에 자격증명 미포함 — 전송 예외 문구가 주소·Authorization·MCP-User-Key 를 실음
    import logging as _logging  # noqa: PLC0415

    class _LeakyTool(_FakeTool):
        async def ainvoke(self, args):
            self.calls.append(args)
            raise ConnectionError(
                "POST https://mcp.test/api/workb/tea000/messages failed · headers="
                "{'Authorization': 'Bearer jwt-test', 'MCP-User-Key': '" + _b64.b64encode(
                    _js.dumps({"client_id": "tea000", "emp_no": "3902172", "timestamp": "x",
                               "request_id": "y", "signature": "z" * 44}).encode()).decode()
                + "'} · secret=s3cret")

    class _Capture(_logging.Handler):
        lines: list[str] = []

        def emit(self, record):
            _Capture.lines.append(record.getMessage())

    _leaky = _LeakyTool("send_memo")
    _use_mcp(tools=[_leaky])
    _mcp.install()
    _handler = _Capture()
    _mcpc.log.addHandler(_handler)
    try:
        _leaked = note.send_note_sync(["3902172"], _note)
    finally:
        _mcpc.log.removeHandler(_handler)
    _texts = [_leaked["detail"], *_Capture.lines]
    check(_leaked["status"] == "failed" and _Capture.lines
          and not any(s in t for t in _texts for s in ("tea000", "jwt-test", "s3cret", "eyJ")),
          "mcp: 실패 문구(로그·화면)에 클라이언트 id·토큰·사용자 키·시크릿이 남지 않는다",
          str(_texts)[:300])
    check("send_memo" in _leaked["detail"] and "ConnectionError" in _leaked["detail"],
          "mcp: 가려도 무엇이 어떻게 실패했는지는 남는다(도구 이름·예외 종류)", _leaked["detail"])
    check(_mcpc.redact("주소 https://gw/workb/tea000/sse", _mcpc.settings())
          == f"주소 https://gw/workb/{_mcpc.REDACTED}/sse",
          "mcp.redact: 설정에 있는 값은 값으로 가린다")

    # SDK 가 install() 때 자기 로거를 INFO 로 되돌려도 감사 JSON 미출력 — install() 직후 재차 낮춤(client._setup_system)
    class _LoudSdk(_FakeSdk):
        @staticmethod
        def install():
            _FakeSdk.log.append(("install",))
            for _n in ("mcp_sdk", "mcp_sdk.audit"):
                _lg = _logging.getLogger(_n)
                _lg.setLevel(_logging.INFO)
                _lg.addHandler(_logging.NullHandler())

    _mcpc.quiet_loggers()
    _use_mcp(tools=[_FakeTool("send_memo")])
    _mcpc.use_backend(sdk=_LoudSdk, adapter=_fake_adapter([_FakeTool("send_memo")]))
    _mcp.install()
    note.send_note_sync(["3902172"], _note)
    check(all(_logging.getLogger(n).getEffectiveLevel() >= _logging.WARNING
              for n in _mcpc.NOISY_LOGGERS),
          "mcp: SDK 설치 뒤에도 감사·httpx 로거의 INFO 가 꺼져 있다(자격증명 JSON 이 안 나간다)",
          str({n: _logging.getLogger(n).getEffectiveLevel() for n in _mcpc.NOISY_LOGGERS}))
    for _n in ("mcp_sdk", "mcp_sdk.audit"):
        for _h in list(_logging.getLogger(_n).handlers):
            _logging.getLogger(_n).removeHandler(_h)

    # 멱등 도구는 재시도(읽기 도구용)
    _readonly = _FakeTool("some_query", fail=1)
    _use_mcp(tools=[_readonly])
    _got = asyncio.run(_mcp.client_for("3902172").call("some_query", {"q": 1}, idempotent=True))
    check(len(_readonly.calls) == 2 and _got,
          "mcp: 다시 불러도 되는 도구는 재시도한다", f"호출 {len(_readonly.calls)}회")

    # 접속 단계 실패는 부수효과 없음 — 항상 재시도
    _late = _FakeTool("send_memo")

    class _FlakyAdapter:
        tries = 0

        def __init__(self, server_list):
            pass

        async def get_tools(self):
            _FlakyAdapter.tries += 1
            if _FlakyAdapter.tries == 1:
                raise ConnectionError("세션 못 엶")
            return [_late]

    _use_mcp(tools=[_late])
    _mcpc.use_backend(sdk=_FakeSdk, adapter=_FlakyAdapter)
    _mcp.install()
    _retried = note.send_note_sync(["3902172"], _note)
    check(_retried["status"] == "sent" and _FlakyAdapter.tries == 2 and len(_late.calls) == 1,
          "mcp: 접속·도구 목록 실패는 다시 붙어 본다(부수효과가 없다)",
          f"{_retried['status']} · 접속 {_FlakyAdapter.tries}회 · 발송 {len(_late.calls)}회")

    # 없는 도구 ≠ 서버 장애
    _use_mcp(tools=[_FakeTool("send_memo")])
    try:
        asyncio.run(_mcp.client_for("3902172").call("없는도구", {}))
        check(False, "mcp: 없는 도구 이름은 무엇이 있는지와 함께 알린다")
    except _mcp.MCPUnavailable as _exc:
        check("send_memo" in str(_exc), "mcp: 없는 도구 이름은 무엇이 있는지와 함께 알린다",
              str(_exc))

    # 보내는 사번 없으면 미발송
    _use_mcp(tools=[_FakeTool("send_memo")])
    os.environ.pop(note.EMP_NO_ENV, None)
    _mcp.install()
    _nobody = note.send_note_sync(["3902172"], _note)
    check(_nobody["status"] == "failed" and "사번" in _nobody["detail"],
          "mcp: 보내는 직원 사번이 없으면 보내지 않고 사유를 말한다", str(_nobody["detail"]))

    # 사번마다 별도 클라이언트 — 감사 기록 분리
    _use_mcp(tools=[_FakeTool("send_memo")])
    check(_mcp.client_for("3902172") is _mcp.client_for("3902172")
          and _mcp.client_for("3902172") is not _mcp.client_for("3902173"),
          "mcp.client_for: 사번마다 클라이언트가 따로이고 같은 사번은 재사용한다")
    try:
        _mcp.MCPClient("")
        check(False, "mcp: 사번 없이는 클라이언트를 만들지 않는다")
    except _mcp.MCPUnavailable:
        check(True, "mcp: 사번 없이는 클라이언트를 만들지 않는다")

    # 진단(/health)에 키 미포함
    check("s3cret" not in _js.dumps(_mcp.stats(), ensure_ascii=False)
          and _mcp.stats()["configured"] is True,
          "mcp.stats: 설정 «여부»만 내보내고 키 값은 내보내지 않는다", str(_mcp.stats()))
    check(_mcp.stats()["packages"] is True and _mcp.unavailable() == "",
          "mcp.stats: 패키지가 있으면 packages 참", str(_mcp.stats()))

    # 설정은 있고 행내 패키지가 없는 조합 — 발송 시점이 아니라 기동 때 판별. 실패키지 환경에서도 재현되도록 backend() 교체
    _real_backend = _mcpc.backend
    _backend_calls = []

    def _no_packages():
        _backend_calls.append(1)
        raise _mcp.MCPUnavailable("행내 mcp_sdk 패키지가 없습니다 — 시험용")

    try:
        _mcpc.use_backend()                     # 주입 해제 — 임포트 시도 상태
        _mcpc.backend = _no_packages
        note.use_sender(None)
        check(_mcp.install() is False and note.SENDER is None,
              "mcp.install: 설정이 갖춰져도 행내 패키지가 없으면 붙이지 않는다 — "
              "발송 시점이 아니라 기동 때 갈린다")
        _s = _mcp.stats()
        check(_s["configured"] is True and _s["packages"] is False and _s["missing"] == [],
              "mcp.stats: 설정과 패키지를 따로 싣는다 — 붙지 않는 이유가 갈린다", str(_s))
        _mcp.unavailable()
        check(len(_backend_calls) == 1,
              "mcp.unavailable: 없다는 사실을 기억한다 — /health 마다 임포트를 다시 시도하지 않는다",
              str(len(_backend_calls)))
    finally:
        _mcpc.backend = _real_backend
        _mcpc.use_backend()

    # 서버 카탈로그
    check(_mcps.parse("") == _mcps.DEFAULT == ("workb",),
          "mcp.servers: 아무것도 안 적으면 workb 하나만 켠다", str(_mcps.parse("")))
    check(_mcps.parse("workb, sybase, 없는서버") == ("workb", "sybase"),
          "mcp.servers: 모르는 이름은 버리고 아는 것만 켠다", str(_mcps.parse("workb, sybase, x")))
    _no_conn = _mcps.endpoints(("workb", "sybase"), base_url="https://h", client_id="tea000")
    check(list(_no_conn) == ["workb-mcp-server"],
          "mcp.servers: 설정이 모자란 서버는 빼고 나머지는 붙인다(전체가 죽지 않게)",
          str(list(_no_conn)))
    _with_conn = _mcps.endpoints(("sybase",), base_url="https://h", client_id="tea000",
                                 conn_id="conn1")
    check(_with_conn["sybase-mcp-server"]["url"] == "https://h/sybase/tea000:conn1",
          "mcp.servers: 접속 id 가 있으면 경로에 붙는다", str(_with_conn))

    # 두 직원 동시 발송 — 각자 자기 사번 컨텍스트(루프 단위 잠금이면 B 가 A 의 컨텍스트를 덮음)
    class _SlowTool(_FakeTool):
        seen: list[tuple[str, str]] = []            # (요청한 사번, SDK 에 서 있던 사번)

        async def ainvoke(self, args):
            mine = args["who"]
            await asyncio.sleep(0.05)               # 다른 스레드 끼어들 틈
            _SlowTool.seen.append((mine, _FakeSdk.current))
            return await super().ainvoke(args)

    _slow = _SlowTool("send_memo")
    _use_mcp(tools=[_slow])
    _errors: list[BaseException] = []

    def _send_as(emp: str) -> None:
        try:
            asyncio.run(_mcp.client_for(emp).call("send_memo", {"who": emp}))
        except BaseException as exc:            # noqa: BLE001 — 스레드 예외 수집
            _errors.append(exc)

    _threads = [_threading.Thread(target=_send_as, args=(emp,)) for emp in ("3902172", "3902174")]
    for _t in _threads:
        _t.start()
        _time.sleep(0.01)                       # A 가 먼저 컨텍스트 설정
    for _t in _threads:
        _t.join(timeout=10)
    check(not _errors and not any(_t.is_alive() for _t in _threads),
          "mcp: 두 스레드의 발송이 예외·교착 없이 끝난다", str(_errors))
    check(sorted(_SlowTool.seen) == [("3902172", "3902172"), ("3902174", "3902174")],
          "mcp: 두 직원이 겹쳐 보내도 각자 자기 사번의 컨텍스트로 나간다 — 프로세스 단위 잠금",
          str(_SlowTool.seen))
finally:
    _mcpc.use_backend()
    _mcpc.issue_token = _saved_issue_token
    _mcp.reset()
    note.use_sender(_saved_sender)
    for _k, _v in _saved_mcp_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
