-- 고정 뷰: 부점별 집계 한 부점당 한 행 (행외 목업 DB용). 개인 식별 정보 없음 — 다른 부점과
-- 견주는 질문은 이 뷰로만 한다.
SELECT b.branch_name,
       CASE WHEN b.branch_code = '{branch_code}' THEN 1 ELSE 0 END AS is_my_branch,
       COUNT(*) AS customer_count,
       SUM(c.eval_amt) AS total_eval_amt,
       ROUND(AVG(c.return_rate), 2) AS avg_return_rate,
       SUM(CASE WHEN c.target_reasons <> '' THEN 1 ELSE 0 END) AS target_count,
       SUM(CASE WHEN c.default_option = '미설정' THEN 1 ELSE 0 END) AS default_option_unset_count,
       SUM(CASE WHEN c.next_maturity_date IS NOT NULL
                 AND julianday(c.next_maturity_date) - julianday('{today}') BETWEEN 0 AND 30
                THEN 1 ELSE 0 END) AS maturity_30d_count
FROM irp_customer_base c
JOIN branch b ON b.branch_code = c.branch_code
GROUP BY b.branch_code, b.branch_name
