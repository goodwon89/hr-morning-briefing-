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

args = sys.argv[1:] or ["DT_1DA7002S"]
if args[0].startswith("search:"):
    code, res = call("https://kosis.kr/openapi/statisticsSearch.do",
                     {"method": "getList", "searchNm": args[0][7:], "resultCount": "30", "startCount": "1"})
    for x in (res if isinstance(res, list) else []):
        print(" ", x.get("ORG_ID"), x.get("TBL_ID"), x.get("TBL_NM"), "|", x.get("STAT_NM"), "|", x.get("STRT_PRD_DE"), "~", x.get("END_PRD_DE"), x.get("PRD_SE", ""))
    if not isinstance(res, list):
        print(code, str(res)[:300])
    sys.exit(0)
if args[0].startswith("raw:"):
    from urllib.parse import parse_qsl
    code, res = call("https://kosis.kr/openapi/Param/statisticsParameterData.do",
                     {"method": "getList", **dict(kv.split("=", 1) for kv in args[0][4:].split(","))})
    if isinstance(res, list):
        for x in res[:80]:
            print("  ", x.get("PRD_DE"), x.get("C1"), x.get("C1_NM"), "|", x.get("C2_NM", ""), "|", x.get("ITM_NM"), x.get("DT"), x.get("UNIT_NM"))
    else:
        print(code, str(res)[:300])
    sys.exit(0)
org = "101"
if args[0].startswith("org:"):
    org, args = args[0][4:], args[1:]
for tbl in args:
    print("=" * 70, "\n", tbl)
    code, meta = call("https://kosis.kr/openapi/statisticsData.do",
                      {"method": "getMeta", "type": "TABLE", "orgId": org, "tblId": tbl})
    print(" TABLE:", code, json.dumps(meta, ensure_ascii=False)[:300])
    code, itm = call("https://kosis.kr/openapi/statisticsData.do",
                     {"method": "getMeta", "type": "ITM", "orgId": org, "tblId": tbl})
    if isinstance(itm, list):
        by = collections.defaultdict(list)
        for x in itm:
            by[(x.get("OBJ_ID"), x.get("OBJ_NM"))].append(f'{x.get("ITM_ID")}={x.get("ITM_NM")}')
        for k, v in by.items():
            print(" ", k, len(v), v[:40])
    else:
        print(" ITM:", code, str(itm)[:300])
    code, data = call("https://kosis.kr/openapi/Param/statisticsParameterData.do",
                      {"method": "getList", "orgId": org, "tblId": tbl, "itmId": "ALL",
                       "objL1": "ALL", "objL2": "", "prdSe": "M", "newEstPrdCnt": "2"})
    if isinstance(data, list):
        print(" DATA rows:", len(data), "keys:", list(data[0].keys()))
        for row in data[:60]:
            print("   ", row.get("PRD_DE"), row.get("C1"), row.get("C1_NM"), row.get("C2_NM", ""),
                  row.get("ITM_ID"), row.get("ITM_NM"), row.get("DT"), row.get("UNIT_NM"))
    else:
        print(" DATA:", code, str(data)[:300])
