import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const monitoring={
 lines:()=>api('/monitoring/lines'),
 baselines:line=>api('/monitoring/baselines?line='+encodeURIComponent(line)),
 createBaseline:data=>api('/monitoring/baselines',{method:'POST',body:JSON.stringify(data)}),
 days:line=>api('/monitoring/days?line='+encodeURIComponent(line)),
 tickets:(line,status)=>api('/monitoring/tickets?line='+encodeURIComponent(line)+(status?'&status='+status:'')),
 ticket:id=>api('/monitoring/tickets/'+id),
 ingest:records=>api('/monitoring/actuals',{method:'POST',body:JSON.stringify({records})}),
 handle:(id,note)=>api('/monitoring/tickets/'+id+'/handles',{method:'POST',body:JSON.stringify({note})}),
 revoke:(id,note)=>api('/monitoring/tickets/'+id+'/revoke',{method:'POST',body:JSON.stringify({note})}),
};
