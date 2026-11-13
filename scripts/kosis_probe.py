"""KOSIS 고용동향 통계표 구조 점검용 (인증키는 출력하지 않음)"""
import os, sys, json, collections, requests

KEY = os.environ.get("KOSIS_API_KEY", "").strip()
if not KEY:
    sys.exit("KOSIS_API_KEY 없음")

def call(url, params):
    params = {**params, "apiKey": KEY, "format": "json", "jsonVD": "Y"}
    r = requests.get(url, params=params, timeout=60)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300].replace(KEY, "***")

for tbl in sys.argv[1:] or ["DT_1DA7002S", "DT_1DA7001S"]:
    print("=" * 70, "\n", tbl)
    code, meta = call("https://kosis.kr/openapi/statisticsData.do",
                      {"method": "getMeta", "type": "TABLE", "orgId": "101", "tblId": tbl})
    print(" TABLE:", code, json.dumps(meta, ensure_ascii=False)[:300])
    code, itm = call("https://kosis.kr/openapi/statisticsData.do",
                     {"method": "getMeta", "type": "ITM", "orgId": "101", "tblId": tbl})
    if isinstance(itm, list):
        by = collections.defaultdict(list)
        for x in itm:
            by[(x.get("OBJ_ID"), x.get("OBJ_NM"))].append(f'{x.get("ITM_ID")}={x.get("ITM_NM")}')
        for k, v in by.items():
            print(" ", k, len(v), v[:40])
    else:
        print(" ITM:", code, str(itm)[:300])
    code, data = call("https://kosis.kr/openapi/Param/statisticsParameterData.do",
                      {"method": "getList", "orgId": "101", "tblId": tbl, "itmId": "ALL",
                       "objL1": "ALL", "objL2": "", "prdSe": "M", "newEstPrdCnt": "2"})
    if isinstance(data, list):
        print(" DATA rows:", len(data), "keys:", list(data[0].keys()))
        for row in data[:60]:
            print("   ", row.get("PRD_DE"), row.get("C1"), row.get("C1_NM"), row.get("C2_NM", ""),
                  row.get("ITM_ID"), row.get("ITM_NM"), row.get("DT"), row.get("UNIT_NM"))
    else:
        print(" DATA:", code, str(data)[:300])
