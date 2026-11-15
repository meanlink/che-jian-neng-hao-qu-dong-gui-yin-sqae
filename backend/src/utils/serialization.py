import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def baseline_dict(b):
    return {k:getattr(b,k) for k in ['id','line','version','job_id','effective_from','threshold_pct','model','status','created_at']}
def actual_dict(a):
    return {k:getattr(a,k) for k in ['id','line','day','revision','features','kwh','received_by','received_at']}
def judgment_dict(j):
    return {k:getattr(j,k) for k in ['id','line','day','baseline_id','revision','actual_kwh','predicted_kwh','deviation_pct','state','replay_id','judged_at']}
def ticket_dict(t):
    return {k:getattr(t,k) for k in ['id','line','baseline_id','start_day','status','opened_day','last_exceed_day','closed_day','close_reason','evidence','replay_id','created_at','updated_at']}
def event_dict(e):
    return {k:getattr(e,k) for k in ['id','ticket_id','actor','kind','note','replay_id','at']}
