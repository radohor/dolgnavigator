from collections import defaultdict
from datetime import datetime

def _dt(v):
    if not v: return datetime.min
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try: return datetime.strptime(v, fmt)
        except ValueError: pass
    return datetime.min

def merge_uid_obligations(records):
    by_uid=defaultdict(list); without=[]
    for r in records:
        uid=(r.get("uid") or "").strip().lower()
        if uid: by_uid[uid].append(r)
        else: without.append(r)
    result=[]
    for uid, rows in by_uid.items():
        rows=sorted(rows,key=lambda r:_dt(r.get("status_date") or r.get("actual_end_date") or r.get("start_date")))
        latest=rows[-1]
        known=[r.get("state") for r in rows if r.get("state") in {"Открыт","Закрыт"}]
        if latest.get("state") in {"Открыт","Закрыт"}:
            state=latest["state"]; basis="latest_creditor_record"
        elif "Открыт" in known:
            state="Открыт"; basis="known_open_record"
        elif known and all(x=="Закрыт" for x in known):
            state="Закрыт"; basis="all_known_records_closed"
        else:
            state="Не определён"; basis="insufficient_chain_facts"
        base=dict(rows[0])
        base.update({
            "uid":uid,"current_creditor":latest.get("creditor"),"obligation_state":state,
            "obligation_state_basis":basis,"same_uid_rows":len(rows),
            "creditor_history":[{"creditor":r.get("creditor"),"date":r.get("status_date") or r.get("actual_end_date") or r.get("start_date"),"state":r.get("state"),"status":r.get("status")} for r in rows]
        })
        result.append(base)
    for r in without:
        b=dict(r); b.update({"current_creditor":r.get("creditor"),"obligation_state":r.get("state") or "Не определён","obligation_state_basis":"no_uid","same_uid_rows":1,"creditor_history":[{"creditor":r.get("creditor"),"date":r.get("start_date"),"state":r.get("state"),"status":r.get("status")}]}); result.append(b)
    return result

def open_contract_count(records):
    return sum(1 for r in merge_uid_obligations(records) if r.get("obligation_state")=="Открыт")
