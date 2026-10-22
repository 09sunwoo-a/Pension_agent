-- 고정 뷰: 우리 부점에서 다른 금융기관으로 계약이전(유출)된 IRP 계좌 한 건당 한 행 (행외 목업 DB용).
-- {branch_code} 는 코드가 직원 사번으로 조회한 부점코드로 채운다 — LLM 이 바꿀 수 없다.
SELECT t.customer_name, b.branch_name, t.transfer_date, t.transfer_amt, t.to_institution
FROM irp_transfer_out t
JOIN branch b ON b.branch_code = t.branch_code
WHERE t.branch_code = '{branch_code}'
