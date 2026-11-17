import {domain} from '../constants/domain.js';
import {research} from '../stores/research.js';
import {session} from '../stores/session.js';
import {blindApi} from '../api/blind.js';
import {sourceOptions} from '../components/common/business.js';
import {table} from '../components/common/table.js';
import {blindBatchStates,blindAppStates} from '../constants/enums.js';
import {esc,formObject,localTime} from '../utils/format.js';
import {action,toast} from '../hooks/actions.js';
const FEATURE_KEYS=['moisture','thickness','temperature','airflow'];
const batchBadge=s=>'<span class="status blind-'+esc(s)+'">'+esc(blindBatchStates[s]||s)+'</span>';
const appBadge=s=>'<span class="status blind-'+esc(s)+'">'+esc(blindAppStates[s]||s)+'</span>';
export async function blindPage(){
  research.blindBatches=await blindApi.listBatches();
  research.blindApplications=await blindApi.listApplications();
  const batches=research.blindBatches,apps=research.blindApplications;
  const active=apps.find(a=>a.id===research.blindAppId)||apps[0];
  research.blindAppId=active?.id??null;
  return '<section class="page-heading"><p class="eyebrow">07 / BLIND & UNBLIND</p><h1>独立盲测批：把实测时长，留到唯一一次揭盲。</h1><p>封存后标签对特征选择、调参与普通查看全部不可见；训练样本与盲测样本按试件编号隔离。先冻结可复现模型、写出预测，再申请一次揭盲。</p></section>'
    +'<div class="blind-layout"><section class="blind-main"><h2>盲测批账册 <small>状态：封存 / 占用 / 已揭盲</small></h2>'
    +(batches.length?batches.map(b=>renderBatch(b,apps)).join(''):'<div class="empty">还没有盲测批。由分析师或负责人在下方封存。</div>')
    +'<h2>盲测申请与误差依据 <small>同批并发只接受一个 · 旧研究仍可读</small></h2>'
    +(apps.length?table(apps.map(a=>({id:a.id,name:a.name,batch_id:a.batch_id,run_id:a.run_id,applicant:a.applicant,state:blindAppStates[a.state]||a.state,attempts:a.attempts,created_at:localTime(a.created_at)})),[{key:'id',label:'申请#'},{key:'name',label:'研究名称'},{key:'batch_id',label:'盲测批#'},{key:'run_id',label:'冻结模型Run#'},{key:'applicant',label:'申请人'},{key:'state',label:'状态'},{key:'attempts',label:'重试次数'},{key:'created_at',label:'创建时间(北京)'}]):'<div class="empty">还没有盲测申请。</div>')
    +(active?renderApplication(active):'')
    +'</section><aside class="entry-sheet">'+(session.writable?renderForms():'<h2>阅读权限</h2><p>可查看盲测批特征、申请进度与已揭盲误差；封存与申请由分析师或负责人执行。实测时长标签封存后对所有人不可见，直至唯一一次揭盲。</p>')+'</aside></div>';
}
function renderBatch(b,apps){
  const linked=apps.filter(a=>a.batch_id===b.id);
  const preview=b.inputs.slice(0,6).map(x=>({reference:x.reference,...x.values}));
  return '<article class="blind-batch"><header><h3>#'+b.id+' '+esc(b.name)+'</h3>'+batchBadge(b.state)+'</header>'
    +'<p class="fine">'+b.input_count+' 条盲测样本 · 输入校验 <code>'+esc(b.input_checksum.slice(0,16))+'…</code> · 标签校验 <code>'+esc(b.labels_checksum.slice(0,16))+'…</code> · 封存 '+esc(localTime(b.sealed_at))+(b.revealed_at?' · 揭盲 '+esc(localTime(b.revealed_at)):'')+'</p>'
    +'<p class="fine">实测时长标签已封存为受限工件，页面与接口均不回显；'+(b.state==='revealed'?'本批已揭盲，下方展示误差依据。':'普通查看、特征选择与调参都接触不到标签。')+'</p>'
    +table(preview,[{key:'reference',label:'试件编号'},{key:'moisture',label:'初始湿度 %'},{key:'thickness',label:'厚度 mm'},{key:'temperature',label:'设定温度 ℃'},{key:'airflow',label:'风量 m³/min'}])
    +(b.input_count>6?'<p class="fine">预览前6条，共 '+b.input_count+' 条。</p>':'')
    +(b.state==='revealed'&&b.result?renderEvidence(b.result):'')
    +(session.writable&&b.state!=='revealed'?'<details><summary>改动盲测输入（会使未揭盲申请失效）</summary><form id="input-form-'+b.id+'" data-batch="'+b.id+'"><p class="fine">逐条提交特征，JSON 数组；试件编号集合必须保持不变。改动后所有未揭盲申请立即失效，冻结模型不重算。</p><textarea name="inputs" rows="5" required>'+esc(JSON.stringify(b.inputs))+'</textarea><button type="submit">提交改动并使申请失效</button></form></details>':'')
    +(linked.length?'<p class="fine">关联申请：'+linked.map(a=>'#'+a.id+'（'+esc(blindAppStates[a.state]||a.state)+'）').join('、')+'</p>':'')
    +'</article>';
}
function renderEvidence(result){
  return '<div class="evidence"><h4>揭盲后的独立误差依据</h4>'
    +'<dl class="metrics">'+result.summary.map(x=>'<div><dt>'+esc(x.label)+'</dt><dd>'+esc(Number.isFinite(x.value)?Number(x.value).toLocaleString('zh-CN',{maximumFractionDigits:4}):x.value)+'</dd></div>').join('')+'</dl>'
    +'<p class="fine">模型校验 <code>'+esc(result.model_checksum.slice(0,16))+'…</code> · 预测校验 <code>'+esc(result.predictions_checksum.slice(0,16))+'…</code> · 标签校验 <code>'+esc(result.labels_checksum.slice(0,16))+'…</code></p>'
    +table(result.rows.slice(0,10),result.columns.map(c=>({key:c,label:domain.column_labels[c]||c})))+'<p class="fine">预览前10条，共 '+result.rows.length+' 条；完整明细用申请处的 CSV 导出。</p></div>';
}
function renderApplication(a){
  return '<article class="blind-app" data-app="'+a.id+'"><h3>申请 #'+a.id+' · '+esc(a.name)+' '+appBadge(a.state)+'</h3>'
    +'<p class="fine">绑定冻结模型 Run #'+a.run_id+'（重试不更换）· 申请人 '+esc(a.applicant)+' · 重试 '+a.attempts+' 次'+(a.model_checksum?' · 模型校验 <code>'+esc(a.model_checksum.slice(0,16))+'…</code>':'')+'</p>'
    +(a.error?'<div class="failure-inline">'+esc(a.error)+'</div>':'')
    +(a.state==='awaiting_unblind'&&a.predictions?'<p class="fine">已写出 '+a.predictions.length+' 条预测（仅编号与预测值，无标签）。现在可申请唯一一次揭盲。</p>':'')
    +(a.state==='revealed'&&a.result?renderEvidence(a.result):'')
    +'<div class="blind-actions">'
    +(a.state==='failed'?'<button data-retry="'+a.id+'">失败重试（绑定原模型）</button>':'')
    +(a.state==='awaiting_unblind'?'<button data-unblind="'+a.id+'">申请一次揭盲 ↗</button>':'')
    +(a.state==='revealed'?'<button class="secondary" data-blind-export="'+a.id+'">下载盲测误差 CSV ↗</button>':'')
    +(a.state==='invalid'?'<span class="fine">申请已失效：盲测输入在揭盲前被改动。请在该批重新发起申请，旧记录保留可读。</span>':'')
    +'</div></article>';
}
function renderForms(){
  const sampleInput=JSON.stringify([{reference:'BLIND-001',values:{moisture:35,thickness:4.2,temperature:75,airflow:3.1}}],null,0);
  return '<h2>封存独立盲测批</h2><form id="seal-form"><label>盲测批名称<input name="name" required value="十一月独立盲测批"></label>'
    +'<label>盲测输入（仅四个特征，禁含 minutes）<textarea name="inputs" rows="5" required>'+esc(sampleInput)+'</textarea></label>'
    +'<label>实测时长标签（仅用于封存，提交后不可回显）<input name="labels" required placeholder="83, 79, 91 … 逗号分隔，条数与输入一致"></label>'
    +'<button type="submit">封存盲测批 →</button><p class="fine">标签写入受限工件（0600），不入库；数据库只保存四个特征列。</p></form>'
    +'<h2>冻结模型并申请揭盲</h2><form id="apply-form"><label>盲测批<select name="batch_id">'+research.blindBatches.filter(b=>b.state!=='revealed').map(b=>'<option value="'+b.id+'">#'+b.id+' '+esc(b.name)+'（'+esc(blindBatchStates[b.state])+'）</option>').join('')+'</select></label>'
    +'<label>训练来源<select name="source_id">'+sourceOptions(research.sources,research.sourceId)+'</select></label>'
    +'<label>研究名称<input name="name" required value="盲测复核研究"></label><div class="parameter-fields">'+domain.params.map(([key,label,val])=>'<label>'+esc(label)+'<input type="number" step="any" name="'+key+'" required value="'+val+'"></label>').join('')+'</div>'
    +'<button type="submit">冻结可复现模型 →</button><p class="fine">申请即冻结训练快照；同批并发只接受一个，之后修改训练数据不改变该模型。</p></form>';
}
export function bindBlind(refresh){
  action('#seal-form',async form=>{
    const d=formObject(form);let inputs;
    try{inputs=JSON.parse(d.inputs)}catch{throw Error('盲测输入必须是 JSON 数组')}
    const labels=d.labels.split(',').map(x=>x.trim()).filter(x=>x!=='').map(Number)
    if(labels.some(x=>!Number.isFinite(x)))throw Error('实测时长必须是逗号分隔的数字');
    await blindApi.sealBatch({name:d.name,inputs,labels});await refresh();toast('盲测批已封存，标签不可再被查看')
  });
  action('#apply-form',async form=>{
    const sealable=research.blindBatches.filter(b=>b.state!=='revealed');
    if(!sealable.length)throw Error('暂无可申请的封存盲测批，请先封存');
    const d=formObject(form);const parameters={};for(const [key]of domain.params)parameters[key]=Number(d[key]);
    const a=await blindApi.apply({batch_id:Number(d.batch_id),source_id:Number(d.source_id),name:d.name,parameters});
    research.blindAppId=a.id;await refresh();toast('模型已提交冻结，盲测批已占用')
  });
  for(const f of document.querySelectorAll('form[id^="input-form-"]'))action('#'+f.id,async form=>{
    const id=form.dataset.batch;let inputs;
    try{inputs=JSON.parse(formObject(form).inputs)}catch{throw Error('输入必须是 JSON 数组')}
    await blindApi.changeInputs(id,inputs);await refresh();toast('盲测输入已改动，未揭盲申请已失效')
  });
  for(const b of document.querySelectorAll('[data-retry]'))b.onclick=async()=>{try{const a=await blindApi.retry(b.dataset.retry);research.blindAppId=a.id;await refresh();toast('已按原模型重新入队')}catch(e){toast(e.message,true)}};
  for(const b of document.querySelectorAll('[data-unblind]'))b.onclick=async()=>{try{const a=await blindApi.unblind(b.dataset.unblind);research.blindAppId=a.id;await refresh();toast('揭盲请求已受理，仅此一次')}catch(e){toast(e.message,true)}};
  for(const b of document.querySelectorAll('[data-blind-export]'))b.onclick=async()=>{try{await blindApi.download(b.dataset.blindExport)}catch(e){toast(e.message,true)}};
}
