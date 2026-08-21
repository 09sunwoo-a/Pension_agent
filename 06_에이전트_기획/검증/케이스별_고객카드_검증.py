# -*- coding: utf-8 -*-
"""케이스별 대표 고객 12명 데이터 정의 + 정합성 검증 V1~V12"""
from datetime import date

ASOF = date(2026, 8, 19)
TAX_LIMIT = 9_000_000
REF_CASH, REF_PRIN = 2.52, 3.02          # F58
CAP = {'안정형':0,'안정추구형':20,'위험중립형':30,'적극투자형':50,'공격투자형':70}   # F17 주식비중 상한
RANK = ['안정형','안정추구형','위험중립형','적극투자형','공격투자형']
DC_MIN = {'초저위험':'안정형','저위험':'안정추구형','중위험':'위험중립형','고위험':'공격투자형'}
DC_NAME = {'초저위험':'안정형 「지켜드림」','저위험':'안정투자형 「알파드림」',
           '중위험':'중립투자형 「뿔려드림」','고위험':'적극투자형 「모두드림」','미설정':'미설정'}

def age(b):
    return ASOF.year-b.year-((ASOF.month,ASOF.day)<(b.month,b.day))
def span(d):
    m=(ASOF.year-d.year)*12+(ASOF.month-d.month)-(1 if ASOF.day<d.day else 0)
    return m//12, m%12
def months(d):
    return (ASOF.year-d.year)*12+(ASOF.month-d.month)-(1 if ASOF.day<d.day else 0)

C = [
 dict(cid='C1', case='현금성자산 편중 — 이탈 고위험', name='노현석',
      birth=date(1975,11,3), open=date(2024,3,18), last=date(2024,3,20), gender='남', star='골드',
      prof='안정추구형', dc='미설정', bal=68_400_000, cash=41_040_000, perf=0, prin=27_360_000,
      equity=0, perfRet=0.0, taxUsed=0,
      retPct=('하위',22), balPct=('상위',6),
      why='퇴직금 입금 후 2년 5개월간 운용지시 없음 — 고유계정대 60% 방치',
      kpi=('고유계정대 비중','60%')),

 dict(cid='C2', case='원리금보장 100% 운용', name='백승주',
      birth=date(1980,4,22), open=date(2020,11,5), last=date(2025,11,14), gender='여', star='프리미엄',
      prof='안정추구형', dc='초저위험', bal=31_500_000, cash=0, perf=0, prin=31_500_000,
      equity=0, perfRet=0.0, taxUsed=3_000_000,
      retPct=('하위',25), balPct=('상위',14),
      why='적립금 전액이 원리금보장상품 — 실적배당 일부 편입 여지',
      kpi=('원리금보장 비중','100%')),

 dict(cid='C3', case='투자성향–디폴트옵션 불일치', name='김자산',
      birth=date(1983,7,12), open=date(2019,3,25), last=date(2025,5,12), gender='남', star='VIP',
      prof='적극투자형', dc='초저위험', bal=42_680_000, cash=4_268_000, perf=12_804_000, prin=25_608_000,
      equity=8_536_000, perfRet=1.50, taxUsed=5_400_000,
      retPct=('하위',18), balPct=('상위',9),
      why='적극투자형인데 디폴트옵션이 초저위험 — 성향과 등록 등급 불일치',
      kpi=('원리금보장성 비중','70%'), std=True),

 dict(cid='C4', case='수익률 저조 (구조 이상 없음)', name='안도경',
      birth=date(1986,9,30), open=date(2021,7,2), last=date(2026,2,3), gender='여', star='로얄',
      prof='위험중립형', dc='중위험', bal=55_200_000, cash=0, perf=19_320_000, prin=35_880_000,
      equity=13_800_000, perfRet=0.50, taxUsed=9_000_000,
      retPct=('하위',14), balPct=('상위',8),
      why='구조는 정상이나 실적배당형 성과가 부진 — 상품 단위 점검 필요',
      kpi=('수익률 백분위','하위 14%')),

 dict(cid='C5', case='성과부진 펀드 보유', name='류지완',
      birth=date(1978,2,17), open=date(2018,5,14), last=date(2026,6,19), gender='남', star='프리미엄',
      prof='적극투자형', dc='중위험', bal=78_500_000, cash=0, perf=31_400_000, prin=47_100_000,
      equity=23_550_000, perfRet=-6.10, taxUsed=6_000_000,
      retPct=('하위',6), balPct=('상위',5),
      why='환매추천 편입 펀드 1,570만원 보유 — 계좌 수익률을 끌어내리는 중',
      kpi=('부진 펀드 비중','20%')),

 dict(cid='C6', case='원리금보장상품 만기 임박', name='남기평',
      birth=date(1971,6,8), open=date(2016,9,22), last=date(2026,8,5), gender='남', star='VIP',
      prof='안정추구형', dc='저위험', bal=124_000_000, cash=0, perf=12_400_000, prin=111_600_000,
      equity=9_920_000, perfRet=5.00, taxUsed=9_000_000,
      retPct=('하위',27), balPct=('상위',3),
      why='정기예금 6,200만원 만기 D-22 — 손실 없이 재설계할 수 있는 유일한 시점',
      kpi=('만기까지','D-22'), maturity=date(2026,9,10), maturityAmt=62_000_000),

 dict(cid='C7', case='세액공제 잔여한도 보유', name='하윤재',
      birth=date(1990,12,5), open=date(2023,1,9), last=date(2025,12,22), gender='여', star='실버',
      prof='위험중립형', dc='중위험', bal=21_300_000, cash=0, perf=8_520_000, prin=12_780_000,
      equity=5_325_000, perfRet=8.50, taxUsed=1_200_000,
      retPct=('하위',41), balPct=('상위',21),
      why='최근 3년 납입 이력 보유 · 당해 세액공제 잔여한도 780만원',
      kpi=('세액공제 잔여','86.7%')),

 dict(cid='C8', case='연금개시 요건 충족 · 미개시', name='구본희',
      birth=date(1968,3,21), open=date(2015,6,10), last=date(2026,7,31), gender='여', star='VIP',
      prof='안정추구형', dc='저위험', bal=187_500_000, cash=9_375_000, perf=37_500_000, prin=140_625_000,
      equity=18_750_000, perfRet=4.80, taxUsed=4_500_000,
      retPct=('하위',29), balPct=('상위',1),
      why='만 58세 · 가입 11년 2개월 — 연금개시 요건 충족, 개시 시점·인출전략 설계 필요',
      kpi=('연금개시','요건 충족 · 미개시')),

 dict(cid='C9', case='ETF 목적 증권사 이전 요구', name='전세라',
      birth=date(1988,8,14), open=date(2019,10,28), last=date(2026,8,14), gender='여', star='로얄',
      prof='공격투자형', dc='고위험', bal=96_400_000, cash=0, perf=67_480_000, prin=28_920_000,
      equity=57_840_000, perfRet=14.50, taxUsed=9_000_000,
      retPct=('상위',21), balPct=('상위',4),
      why='ETF 라인업을 이유로 증권사 이전 문의 — 8.14 상담 이력',
      kpi=('실적배당 비중','70%'), signal='상담 이력'),

 dict(cid='C10', case='수수료 불만', name='표민교',
      birth=date(1974,10,9), open=date(2017,9,14), last=date(2026,8,11), gender='남', star='골드',
      prof='위험중립형', dc='중위험', bal=63_700_000, cash=0, perf=22_295_000, prin=41_405_000,
      equity=15_925_000, perfRet=6.40, taxUsed=2_400_000,
      retPct=('하위',34), balPct=('상위',7),
      why='수수료 불만 접수 — 대면 개설 계좌, 비대면 전환 시 인하 여지',
      kpi=('가입 경과','8년 11개월'), signal='상담 이력'),

 dict(cid='C11', case='수익률 · 관리 불만', name='양승훈',
      birth=date(1982,5,26), open=date(2022,4,11), last=date(2026,7,28), gender='남', star='실버',
      prof='위험중립형', dc='중위험', bal=38_900_000, cash=1_945_000, perf=13_615_000, prin=23_340_000,
      equity=10_892_000, perfRet=-1.80, taxUsed=0,
      retPct=('하위',11), balPct=('상위',11),
      why='콜센터 수익률 불만 접수 — 실적배당형 −1.80%, 개설 후 대면 접촉 없음',
      kpi=('수익률 백분위','하위 11%'), signal='상담 이력'),

 dict(cid='C12', case='연금수령 중 — 투자상품 권유 제외', name='도영란',
      birth=date(1963,1,19), open=date(2013,2,15), last=date(2026,4,16), gender='여', star='VIP',
      prof='안정추구형', dc='초저위험', bal=142_300_000, cash=7_115_000, perf=21_345_000, prin=113_840_000,
      equity=14_230_000, perfRet=3.90, taxUsed=None,
      retPct=('하위',26), balPct=('상위',2),
      why='연금수령 중 — 투자상품 권유 제외 대상, 수령 관리와 수수료 면제 적용 확인',
      kpi=('연금수령','수령 중'), payout=True),
]

# ---------- 파생값 ----------
for c in C:
    c['age']=age(c['birth']); c['spanY'],c['spanM']=span(c['open']); c['lastM']=months(c['last'])
    c['ret1y']=round(c['cash']/c['bal']*REF_CASH + c['prin']/c['bal']*REF_PRIN + c['perf']/c['bal']*c['perfRet'], 2)
    c['safeRatio']=round((c['prin']+c['cash'])/c['bal']*100, 1)
    c['eqRatio']=round(c['equity']/c['bal']*100, 1)
    c['taxRemain']=None if c['taxUsed'] is None else TAX_LIMIT-c['taxUsed']
    c['taxPct']=None if c['taxUsed'] is None else round(c['taxRemain']/TAX_LIMIT*100, 1)
    for k in ('cash','perf','prin'):
        c[k+'Pct']=round(c[k]/c['bal']*100, 1)

# ---------- 태그 ----------
TAGP=['이탈위험','이탈신호','연금수령 중','만기 임박','성향–디폴트옵션 불일치','디폴트옵션 미설정',
      '성과부진 상품 보유','수익률 하위','원리금보장 편중','세액공제 잔여','연금개시 가능','장기 미접촉']
def tags(c):
    t=[]
    if c['bal']>=10_000_000 and c['cashPct']>=50: t.append('이탈위험')
    if c.get('signal'): t.append('이탈신호')
    if c.get('payout'): t.append('연금수령 중')
    if c.get('maturity'): t.append('만기 임박')
    if c['dc']=='초저위험' and RANK.index(c['prof'])>=RANK.index('위험중립형'): t.append('성향–디폴트옵션 불일치')
    if c['dc']=='미설정': t.append('디폴트옵션 미설정')
    if c['cid']=='C5': t.append('성과부진 상품 보유')
    if c['retPct'][0]=='하위' and c['retPct'][1]<=20: t.append('수익률 하위')
    if c['safeRatio']>=70: t.append('원리금보장 편중')
    if c['taxPct'] is not None and c['taxPct']>=50: t.append('세액공제 잔여')
    if c['age']>=55 and c['spanY']>=5 and not c.get('payout'): t.append('연금개시 가능')
    if c['lastM']>=12: t.append('장기 미접촉')
    return sorted(t, key=TAGP.index)

for c in C: c['tags']=tags(c); c['tagsShown']=c['tags'][:3]

# ---------- 검증 ----------
def verify():
    f=[]
    for c in C:
        i=c['cid']
        if c['cash']+c['perf']+c['prin']!=c['bal']: f.append(f'{i} V1 3구분 합≠평가금액')
        if abs(c['cashPct']+c['perfPct']+c['prinPct']-100.0)>0.05: f.append(f"{i} V2 비중합 {c['cashPct']+c['perfPct']+c['prinPct']}")
        if c['taxUsed'] is not None and c['taxUsed']+c['taxRemain']!=TAX_LIMIT: f.append(f'{i} V5 세액공제 합≠한도')
        if c['dc']!='미설정' and RANK.index(c['prof'])<RANK.index(DC_MIN[c['dc']]): f.append(f"{i} V7 {c['prof']}는 {c['dc']} 가입 불가")
        if c['eqRatio']>CAP[c['prof']]: f.append(f"{i} V8 주식 {c['eqRatio']}% > 상한 {CAP[c['prof']]}%")
        if c['eqRatio']>70: f.append(f'{i} V8 위험자산 70% 한도 초과')
        w=(c['cash']*REF_CASH+c['prin']*REF_PRIN+c['perf']*c['perfRet'])/c['bal']
        if abs(w-c['ret1y'])>0.5: f.append(f'{i} V11 수익률 불일치')
        if c.get('payout') and c['taxUsed'] is not None: f.append(f'{i} 수령 중인데 세액공제 표시')
        if len(c['tagsShown'])>3: f.append(f'{i} 태그 3개 초과')
        if not c['tagsShown']: f.append(f'{i} 태그 없음')
    # V13 수익률-백분위 단조성
    def rk(c): return c['retPct'][1] if c['retPct'][0]=='하위' else 100-c['retPct'][1]
    s=sorted(C,key=lambda c:c['ret1y'])
    for a,b in zip(s,s[1:]):
        if rk(a)>rk(b): f.append(f"V13 {a['cid']}({a['ret1y']}%,{rk(a)}) > {b['cid']}({b['ret1y']}%,{rk(b)})")
    return f

if __name__ == '__main__':
    f = verify()
    print('검증 실패:', f if f else '없음 — 12명 전원 통과')
