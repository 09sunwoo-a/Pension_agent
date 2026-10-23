-- 고정 뷰: 우리 부점의 IRP 고객 한 명당 한 행 — **전월말 스냅샷** (행외 목업 DB용).
-- 컬럼은 v_irp_customer 와 같다. 남은 일수는 전월말({prev_date}) 기준이다.
-- {branch_code} 는 코드가 직원 사번으로 조회한 부점코드로 채운다 — LLM 이 바꿀 수 없다.
SELECT NULL AS customer_ref,
       c.customer_name, c.age, c.star_grade, c.risk_profile,
       b.branch_name, c.manager_emp_no, c.joined_date,
       c.eval_amt, c.return_rate, c.safe_asset_pct, c.idle_cash_pct,
       c.default_option, c.pension_started, c.pension_eligible,
       c.next_maturity_date, c.next_maturity_amt,
       CAST(julianday(c.next_maturity_date) - julianday('{prev_date}') AS INTEGER) AS days_to_maturity,
       c.last_contact_days, c.target_reasons,
       CASE WHEN c.target_reasons <> '' THEN 1 ELSE 0 END AS is_target,
       c.idle_cash_amt, c.idle_cash_days, c.tax_credit_room, c.transfer_inquiry,
       c.risk_mismatch, c.isa_maturity_date, c.isa_maturity_amt, c.etf_eval_amt, c.next_maturity_type, c.pension_savings,
       CAST(julianday(c.isa_maturity_date) - julianday('{prev_date}') AS INTEGER) AS isa_days_to_maturity
FROM irp_customer_prev c
JOIN branch b ON b.branch_code = c.branch_code
WHERE c.branch_code = '{branch_code}'
