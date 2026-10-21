-- 직원 사번 → 소속 부점. {emp_no} 는 코드가 7자리 숫자인지 확인한 뒤 채운다.
SELECT e.branch_code, b.branch_name
FROM employee e JOIN branch b ON b.branch_code = e.branch_code
WHERE e.emp_no = '{emp_no}'
