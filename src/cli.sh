# CLI 단축 정의 — 실행이 아니라 «읽어 들이는» 파일이다.
#
#     cd src && source ./cli.sh
#
# 행내에 코드를 들고 갈 때마다 README 에서 세 줄을 찾아 붙여넣지 않으려고 둔다.
# 서버(run_local.sh)와는 상관이 없다 — 아래 셋은 HTTP 를 타지 않고 graph.ask() 를
# 직접 부른다. .env 만 잡혀 있으면 행내에서도 사외에서와 똑같이 돈다.

if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  echo "이 파일은 실행하지 말고 읽어 들이십시오:  source ./cli.sh" >&2
  exit 1
fi

CA="python -m pension_agent.consult_agent"     # 상담 대화 (운영 진입점과 같은 경로)
CAD="python -m tests.debug"                    # 같은 것 + 트레이스 (--debug)
CADR="python -m tests.debug.reps"              # 대표 질문 묶음 · 시연 대본
export CA CAD CADR

cat <<'USAGE'
CA · CAD · CADR 준비됐습니다. 인자 규약은 셋 다 같습니다.

  $CA "고객이 주식이 더 낫다는데 뭐라고 하지?"          단발
  $CA -c 198734-1205842                                REPL (고객 화면이 열린 상태)
  $CA -c 198734-1205842 "투자성향 뭐야?" "만기 자금은?"  멀티턴을 한 줄로 (맥락 이어서)

  $CAD --debug "세액공제 한도가 얼마야?"                + 전체 트레이스 (어디서 갈렸나)
  $CAD --debug --show-llm ...                          + 폐기된 생성문까지 (왜 잘렸나)
  $CAD --list                                          캔드 시나리오 목록 (LLM 키 없이)

  $CADR                                                검토 12케이스 + 요약표
  $CADR demo --why                                     시연 대본 16턴 리허설 ($CADR --help)

어느 .env 가 읽혔는지:  python -m pension_agent.env
USAGE
