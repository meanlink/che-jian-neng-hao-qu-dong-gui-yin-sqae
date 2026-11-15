import {jobs,monitoring} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {TicketStatus,DayState,CloseReason,EventKind} from '../constants/enums.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const pill=s=>'<span class="pill '+esc(s)+'">'+esc(TicketStatus[s]||s)+'</span>';
const TABS=[['pending','待评估'],['open','异常'],['recovered','恢复'],['revoked','撤销']];
const basisCols=[{key:'day',label:'业务日期'},{key:'revision',label:'修订号'},{key:'actual_kwh',label:'实测kWh'},{key:'predicted_kwh',label:'预测kWh'},{key:'deviation_pct',label:'偏差%'}];

function ticketItem(t,versions){
 const range=t.closed_day?esc(t.start_day)+' → '+esc(t.closed_day):esc(t.start_day)+' 起';
 const opened=t.opened_day?'开单 '+esc(t.opened_day):'未满连续三日';
 return '<button class="ticket-item" data-ticket="'+t.id+'"><div><strong>#'+t.id+' · '+esc(t.line)+'</strong>'+pill(t.status)+'</div><span>'+range+' · '+opened+' · 基线v'+(versions[t.baseline_id]||'?')+'</span><small>'+esc(CloseReason[t.close_reason]||'')+(t.close_reason?' · ':'')+'最近超出 '+esc(t.last_exceed_day||'—')+'</small></button>';
}

function detailHtml(d,versions){
 const ev=d.evidence||{};
 const manual=d.events.filter(e=>e.actor!=='system'&&['note','manual_revoked'].includes(e.kind));
 return '<div class="ticket-detail"><div class="section-title"><h2>异常单 #'+d.id+' 处置与依据</h2><span>replay '+esc((d.replay_id||'').slice(0,12))+' · 更新于 '+date(d.updated_at)+'</span></div>'
 +'<div class="detail-grid"><section><h3>状态</h3><p>'+pill(d.status)+' 起始于 '+esc(d.start_day)+'，'+(d.opened_day?'开单日 '+esc(d.opened_day):'尚未开单')+'，'+(d.closed_day?'关闭日 '+esc(d.closed_day):'未关闭')+'。关闭原因：'+esc(CloseReason[d.close_reason]||'—')+'。基线版本 v'+esc(versions[d.baseline_id]||'?')+'，日判断与异常起止对应同一输入版本（replay '+esc((d.replay_id||'').slice(0,12))+'）。</p>'
 +'<h3>开单依据（连续超出三日）</h3>'+table(basisCols,ev.open_basis||[],'暂无超出记录。')
 +(ev.close_basis&&ev.close_basis.length?'<h3>恢复依据（连续两日回到阈值内）</h3>'+table(basisCols,ev.close_basis):'')
 +'</section><section><h3>事件与人工处置</h3><ul class="timeline">'+d.events.map(e=>'<li><strong>'+esc(EventKind[e.kind]||e.kind)+' · '+esc(e.note)+'</strong><span>'+esc(e.actor)+' · '+date(e.at)+'</span></li>').join('')+'</ul>'
 +(session.user.role!=='viewer'?'<form id="handle-form"><label>人工处置记录<textarea name="note" rows="2" required minlength="2" placeholder="例如：已通知设备组现场检查"></textarea></label><button type="submit" class="button small">追加处置记录</button>'+(session.user.role==='admin'&&['pending','open'].includes(d.status)?'<button type="button" class="button small" id="revoke-ticket">撤销误报</button>':'')+'</form>':'')
 +(manual.length?'':'<p class="hint">尚无人工处置记录。</p>')+'</section></div></div>';
}

export async function monitorPage(){
 const lines=await monitoring.lines();
 const writable=session.user.role!=='viewer';
 if(!lines.length){
  const approvedJobs=session.user.role==='admin'?await jobs.list():[];
  return heading('05 / LINE WATCH','线体监测','把批准的归因结果设为监测基线，持续接入带修订号的日实测，自动开具与关闭异常单。')
  +'<section class="panel empty">尚无监测线体。请先在结果复核页批准一次分析，然后由负责人在此设置监测基线。</section>'+(session.user.role==='admin'?'<section class="panel">'+baselineForm(approvedJobs,null)+'</section>':'');
 }
 const line=lines.some(l=>l.line===workspace.monitorLine)?workspace.monitorLine:lines[0].line;
 workspace.monitorLine=line;
 const current=lines.find(l=>l.line===line);
 const tab=TABS.some(t=>t[0]===workspace.monitorTab)?workspace.monitorTab:'open';
 workspace.monitorTab=tab;
 const [days,tickets,baselines,allJobs]=await Promise.all([monitoring.days(line),monitoring.tickets(line),monitoring.baselines(line),session.user.role==='admin'?jobs.list():Promise.resolve([])]);
 const versions={};baselines.forEach(b=>versions[b.id]=b.version);
 const filtered=tickets.filter(t=>t.status===tab);
 let detail='';
 if(workspace.monitorTicket){
  try{detail=detailHtml(await monitoring.ticket(workspace.monitorTicket),versions);}catch(e){workspace.monitorTicket=null;}
 }
 const asc=[...days].reverse();
 const trend={type:'line',labels:asc.map(j=>j.day),series:[{name:'实测kWh',values:asc.map(j=>j.actual_kwh)},{name:'预测kWh',values:asc.map(j=>j.predicted_kwh)}]};
 const dayCols=[{key:'day',label:'业务日期'},{key:'state',label:'状态'},{key:'revision',label:'修订号'},{key:'actual_kwh',label:'实测kWh'},{key:'predicted_kwh',label:'预测kWh'},{key:'deviation_pct',label:'偏差%'},{key:'baseline',label:'基线'},{key:'replay_id',label:'回放号'}];
 const dayRows=days.map(j=>({...j,state:DayState[j.state]||j.state,baseline:j.baseline_id?'v'+(versions[j.baseline_id]||'?'):'—',replay_id:(j.replay_id||'').slice(0,8)}));
 const b=current.baseline;
 return heading('05 / LINE WATCH','线体监测','已批准结果设为监测基线并固定预测依据；日实测按修订号接入，连续三日超出阈值开异常，连续两日恢复关闭，缺日不算恢复。','<button class="button" id="refresh-monitor">刷新 ↻</button>')
 +'<div class="line-tabs">'+lines.map(l=>'<button class="button small'+(l.line===line?' primary':'')+'" data-line="'+esc(l.line)+'">'+esc(l.line)+(l.counts.open?' · 异常 '+l.counts.open:'')+'</button>').join('')+'</div>'
 +'<section class="overview-hero monitor-hero"><div class="narrative"><p class="eyebrow">当前基线</p><h2>'+(b?'v'+b.version+' · 偏差阈值 ±'+fmt(b.threshold_pct)+'% · 自 '+esc(b.effective_from)+' 生效':'尚无基线，日实测处于待评估状态')+'</h2><p>'+(b?'预测依据冻结自「'+esc(b.model.job_name)+'」，入选特征 '+esc(b.model.features.join('、'))+'；换版按生效日分段，跨版不拼连续天数。':'由负责人选择已批准结果设置基线。')+'</p></div><div class="numbers">'+TABS.map(t=>'<div><span>'+t[1]+'</span><strong>'+(current.counts[t[0]]||0)+'</strong></div>').join('')+'</div></section>'
 +'<section class="panel"><div class="section-title"><h2>异常单</h2><span>共 '+tickets.length+' 单 · 迟到或更正按业务日期重演受影响区间</span></div><div class="line-tabs">'+TABS.map(t=>'<button class="button small'+(t[0]===tab?' primary':'')+'" data-tab="'+t[0]+'">'+t[1]+' '+(current.counts[t[0]]||0)+'</button>').join('')+'</div><div class="ticket-list">'+(filtered.length?filtered.map(t=>ticketItem(t,versions)).join(''):'<div class="empty">该状态下暂无单据。</div>')+'</div>'+detail+'</section>'
 +'<section class="panel"><div class="section-title"><h2>逐日依据</h2><span>最近 '+days.length+' 天 · 实测与基线预测</span></div>'+chart(trend)+table(dayCols,dayRows,'尚无逐日判断。')+'</section>'
 +'<div class="two-columns"><section class="panel"><div class="section-title"><h2>接入日实测</h2><span>带修订号 · 迟到或更正自动重演</span></div><form id="actual-form"><div class="parameter-grid"><label>业务日期<input type="date" name="day" required></label><label>修订号<input type="number" name="revision" min="1" step="1" value="1" required></label></div><div class="parameter-grid"><label>日产量<input type="number" step="any" name="units" required></label><label>运行小时<input type="number" step="any" name="run_hours" required></label></div><div class="parameter-grid"><label>环境温度℃<input type="number" step="any" name="temp_c" required></label><label>待机小时<input type="number" step="any" name="idle_hours" required></label></div><label>日电耗 kWh<input type="number" step="any" name="kwh" required></label><button type="submit" class="primary" '+(writable?'':'disabled')+'>校验并接入 '+esc(line)+'</button><p class="hint">同一业务日期用更高修订号更正；同键同内容重试幂等跳过，不重复开单。</p></form></section>'
 +'<section class="panel"><div class="section-title"><h2>基线版本</h2><span>'+baselines.length+' 版</span></div><div class="schedule-list">'+baselines.map(v=>'<article><div><strong>v'+v.version+' · ±'+fmt(v.threshold_pct)+'%</strong><p>自 '+esc(v.effective_from)+' 生效 · 来源「'+esc(v.model.job_name)+'」</p><small>'+(v.status==='active'?'当前生效':'已被换版')+' · '+date(v.created_at)+'</small></div></article>').join('')+'</div>'+(session.user.role==='admin'?baselineForm(allJobs,line):'<p class="hint">仅负责人可设置或更换基线版本。</p>')+'</section></div>';
}

function baselineForm(allJobs,line){
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed'&&j.result&&j.result.model);
 return '<form id="baseline-form"><div class="section-title"><h2>设置监测基线</h2></div><label>已批准分析<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><div class="parameter-grid"><label>线体<input name="line" value="'+esc(line||'冲压P01')+'" required minlength="2"></label><label>生效日<input type="date" name="effective_from" required></label></div><label>偏差阈值 %<input type="number" step="any" name="threshold_pct" value="8" min="0.1" max="50" required></label><button type="submit" class="primary" '+(approved.length?'':'disabled')+'>设为基线并重演</button><p class="hint">换版后按生效日分段判断，跨版不拼连续天数；阈值随版本冻结。</p></form>';
}

export function bindMonitor(refresh){
 document.querySelector('#refresh-monitor')?.addEventListener('click',refresh);
 document.querySelectorAll('[data-line]').forEach(b=>b.onclick=()=>{workspace.monitorLine=b.dataset.line;workspace.monitorTicket=null;refresh();});
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{workspace.monitorTab=b.dataset.tab;workspace.monitorTicket=null;refresh();});
 document.querySelectorAll('[data-ticket]').forEach(b=>b.onclick=()=>{workspace.monitorTicket=Number(b.dataset.ticket);refresh();});
 const af=document.querySelector('#actual-form');
 if(af)submit(af,async f=>{
  const rec={line:workspace.monitorLine,day:f.get('day'),revision:Number(f.get('revision')),units:Number(f.get('units')),run_hours:Number(f.get('run_hours')),temp_c:Number(f.get('temp_c')),idle_hours:Number(f.get('idle_hours')),kwh:Number(f.get('kwh'))};
  const r=await monitoring.ingest([rec]);notify('已接入 '+r.ingested+' 条，幂等跳过 '+r.skipped+' 条，受影响区间已重演');await refresh();
 });
 const bf=document.querySelector('#baseline-form');
 if(bf)submit(bf,async f=>{
  await monitoring.createBaseline({job_id:Number(f.get('job_id')),line:f.get('line'),effective_from:f.get('effective_from'),threshold_pct:Number(f.get('threshold_pct'))});
  notify('基线已生效，监测区间已按新版本重演');await refresh();
 });
 const hf=document.querySelector('#handle-form');
 if(hf)submit(hf,async f=>{
  await monitoring.handle(workspace.monitorTicket,f.get('note'));notify('处置记录已保存');await refresh();
 });
 document.querySelector('#revoke-ticket')?.addEventListener('click',async()=>{
  const note=document.querySelector('#handle-form [name=note]')?.value||'人工判定为误报';
  if(note.trim().length<2){notify('请先填写撤销说明',true);return;}
  try{await monitoring.revoke(workspace.monitorTicket,note);notify('已撤销，人工处置记录保留');await refresh();}catch(e){notify(e.message,true);}
 });
}
