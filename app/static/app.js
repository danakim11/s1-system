const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let state = null;
let activeWindow = "recent10";
let displayCandidates = null;
let scanningCandidates = false;

const money = (value) => `${new Intl.NumberFormat("ko-KR", {maximumFractionDigits:0}).format(value || 0)}원`;
const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const api = async (url, options={}) => {
  const response = await fetch(url, {headers:{"Content-Type":"application/json"}, ...options});
  const body = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(body.detail || "요청에 실패했습니다.");
  return body;
};
function toast(message){const el=$("#toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}

async function refresh(){
  state = await api("/api/dashboard");
  render();
}
function render(){
  const {settings, engine, today, trades} = state;
  $("#current-level").textContent=settings.applied_level;
  $("#recommendation-copy").textContent=`추천 ${state.recommended_level}% · ${state.recommendation_reason}`;
  $$("[data-level]").forEach(b=>b.classList.toggle("active",Number(b.dataset.level)===settings.applied_level));
  $("#today-count").textContent=today.count; $("#today-winrate").textContent=pct(today.win_rate);
  $("#today-expectancy").textContent=pct(today.expectancy); $("#today-pnl").textContent=money(today.cumulative_pnl);
  const strip=$("#result-strip"); const recent=trades.slice(-20);
  strip.innerHTML=recent.length?recent.map(t=>`<span class="trade-dot ${t.return_pct<0?'loss':''}" title="${t.stock_name} ${pct(t.return_pct)}"></span>`).join(""):'<span class="empty-copy">거래가 기록되면 결과가 표시됩니다</span>';
  renderEngine(engine);
  Object.entries(settings).forEach(([key,value])=>{const input=$(`[name="${key}"]`);if(input)input.value=value});
  renderPerformance(); renderTrades(trades); if(displayCandidates!==null)renderCandidates(displayCandidates);
}
function renderEngine(engine){
  const active=engine.running && engine.loop_active;
  $("#status-dot").classList.toggle("live",active);
  $("#engine-label").textContent=engine.last_error || (engine.running && !engine.loop_active)?"엔진 실행 오류":active?"엔진 실행 중":"엔진 정지";
  $("#start-button").textContent=engine.running?"엔진 정지":"엔진 시작";
  $("#execution-title").textContent=engine.armed?"실주문 활성":"안전 잠금";
  $("#execution-detail").textContent=engine.last_error || (engine.armed?"조건 충족 시 주문 전송":engine.live_enabled?"실주문 대기":"운영 주문 비활성");
  $("#arm-button").textContent=engine.armed?"실주문 잠금":"실주문 활성화";
}
function renderPerformance(){
  const perf=state[activeWindow];
  $("#recent-winrate").textContent=pct(perf.win_rate); $("#recent-win").textContent=pct(perf.average_win);
  $("#recent-loss").textContent=pct(perf.average_loss); $("#recent-expectancy").textContent=pct(perf.expectancy);
  drawChart(state.trades);
}
let curvePoints=[];
let allCurvePoints=[];
let curveDays=0;
let curveLoading=false;
let curveSelection=null;
async function refreshCurve(){
  if(curveLoading)return;
  curveLoading=true;
  try{allCurvePoints=await api("/api/equity-curve");applyCurvePeriod()}
  catch(error){$("#curve-detail").textContent="수익 곡선 조회 실패 · "+error.message}
  finally{curveLoading=false}
}
function applyCurvePeriod(){
  const today=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Seoul"}).format(new Date());
  const start=new Date(today+"T00:00:00+09:00");start.setUTCDate(start.getUTCDate()-Math.max(0,curveDays-1));
  const end=new Date(today+"T00:00:00+09:00");end.setUTCDate(end.getUTCDate()+1);
  let cumulative=0;
  curvePoints=allCurvePoints.filter(p=>!curveDays||(new Date(p.exited_at)>=start&&new Date(p.exited_at)<end)).map(p=>({...p,cumulative_pnl:cumulative+=Number(p.realized_pnl)}));
  curveSelection=null;
  $("#curve-period-label").textContent=(curveDays?`오늘 포함 최근 ${curveDays}일 · 기간 내 손익을 0원부터 누적`:"전체 청산 거래 · 누적 실현손익")+" · 원 기준";
  $$("[data-curve-days]").forEach(b=>b.classList.toggle("active",Number(b.dataset.curveDays)===curveDays));
  drawChart();
}
$$("[data-curve-days]").forEach(b=>b.addEventListener("click",()=>{curveDays=Number(b.dataset.curveDays);applyCurvePeriod()}));
$("#curve-days-form").addEventListener("submit",event=>{event.preventDefault();if(!event.currentTarget.reportValidity())return;curveDays=Number($("#curve-days").value);applyCurvePeriod()});
function drawChart(){
  const canvas=$("#equity-chart"),ctx=canvas.getContext("2d"),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  if(rect.width<1)return;
  canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.scale(dpr,dpr);
  const values=[0,...curvePoints.map(p=>Number(p.cumulative_pnl))];
  const total=values.at(-1),label=$("#curve-total");label.textContent=money(total);label.className=total<0?"negative":"positive";
  const left=76,right=16,top=20,bottom=30,w=Math.max(1,rect.width-left-right),h=rect.height-top-bottom;
  let low=0,high=0;for(const value of values){low=Math.min(low,value);high=Math.max(high,value)}
  const margin=(high-low||100)*.12;low-=margin;high+=margin;
  const x=i=>left+i*w/Math.max(1,values.length-1),y=v=>top+(high-v)*h/(high-low);
  ctx.font="11px system-ui";
  for(let i=0;i<=4;i++){
    const value=low+(high-low)*i/4,py=y(value);ctx.strokeStyle="#49354c";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(left+w,py);ctx.stroke();
    ctx.fillStyle="#bba8bd";ctx.textAlign="right";ctx.fillText(new Intl.NumberFormat("ko-KR",{notation:"compact",maximumFractionDigits:1}).format(value),left-9,py+4);
  }
  ctx.setLineDash([4,4]);ctx.strokeStyle="#bba8bd";ctx.beginPath();ctx.moveTo(left,y(0));ctx.lineTo(left+w,y(0));ctx.stroke();ctx.setLineDash([]);
  ctx.textAlign="left";ctx.fillText("시작",left,rect.height-7);ctx.textAlign="right";ctx.fillText(`${curvePoints.length}회 청산`,left+w,rect.height-7);
  if(!curvePoints.length){$("#curve-detail").textContent="선택한 기간에 청산된 거래가 없습니다.";return}
  ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));
  ctx.lineTo(x(values.length-1),y(0));ctx.lineTo(left,y(0));ctx.closePath();
  const gradient=ctx.createLinearGradient(0,top,0,top+h);gradient.addColorStop(0,"#f3a6c840");gradient.addColorStop(1,"#f3a6c805");ctx.fillStyle=gradient;ctx.fill();
  ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.strokeStyle="#f3a6c8";ctx.lineWidth=2.5;ctx.stroke();
  const index=curveSelection===null?values.length-1:Math.max(1,Math.min(values.length-1,curveSelection)),point=curvePoints[index-1];
  ctx.fillStyle="#fff2f8";ctx.beginPath();ctx.arc(x(index),y(values[index]),4,0,Math.PI*2);ctx.fill();
  $("#curve-detail").textContent=`${new Date(point.exited_at).toLocaleString("ko-KR")} · ${point.stock_name} · 거래 손익 ${money(point.realized_pnl)} · 누적 ${money(point.cumulative_pnl)}`;
  canvas.setAttribute("aria-label",`${curveDays?"선택 기간":"전체"} ${curvePoints.length}회 청산 거래, 누적 실현손익 ${money(total)}`);
}
$("#equity-chart").addEventListener("pointermove",event=>{
  const rect=event.currentTarget.getBoundingClientRect();curveSelection=Math.round((event.clientX-rect.left-76)/Math.max(1,rect.width-92)*curvePoints.length);drawChart();
});
$("#equity-chart").addEventListener("pointerleave",()=>{curveSelection=null;drawChart()});
refreshCurve();
setInterval(refreshCurve,10000);
function renderTrades(trades){
  const body=$("#trade-body"); if(!trades.length){body.innerHTML='<tr><td colspan="7" class="empty-cell">아직 기록된 거래가 없습니다</td></tr>';return}
  body.innerHTML=[...trades].reverse().slice(0,20).map(t=>`<tr><td>${new Date(t.exited_at).toLocaleDateString("ko-KR")}</td><td><b>${t.stock_name}</b><br><small>${t.stock_code}</small></td><td>${t.level}%</td><td>-${t.stop_loss_rate}%</td><td><span class="badge">${t.exit_reason==="BROKER_EXIT"?"키움 매도":t.exit_reason}</span></td><td class="${t.return_pct>=0?'positive':'negative'}">${pct(t.return_pct)}</td><td>${money(t.realized_pnl)}</td></tr>`).join("");
}
function renderCandidates(items){
  const body=$("#candidate-body");if(!items.length){body.innerHTML='<tr><td colspan="6" class="empty-cell">설정한 진입 등락률에 근접한 후보가 없습니다</td></tr>';return}
  body.innerHTML=items.map(c=>`<tr><td><button type="button" class="stock-chart-link" data-chart-code="${escapeText(c.stock_code)}" data-chart-name="${escapeText(c.stock_name)}">${escapeText(c.stock_name)}</button><br><small>${escapeText(c.stock_code)}</small></td><td>${money(c.price).replace('원','')}</td><td class="positive">+${pct(c.change_rate)}</td><td>${c.turnover_eok.toLocaleString()}억</td><td>${c.market_cap_eok.toLocaleString()}억</td><td><span class="badge ${c.allowed?'allowed':''}">${c.allowed?'조건 충족':(c.reason||'관찰')}</span></td></tr>`).join("");
}

$$("[data-level]").forEach(button=>button.addEventListener("click",async()=>{const next={...state.settings,applied_level:Number(button.dataset.level)};await api("/api/settings",{method:"PUT",body:JSON.stringify(next)});toast(`${next.applied_level}% 적용`);refresh()}));
$$("[data-window]").forEach(button=>button.addEventListener("click",()=>{activeWindow=button.dataset.window;$$('[data-window]').forEach(b=>b.classList.toggle('active',b===button));renderPerformance()}));
$("#save-button").addEventListener("click",async()=>{const form=new FormData($("#settings-form"));const next={...state.settings};for(const [key,value] of form){next[key]=["entry_rate","stop_loss_rate","min_turnover_eok","min_market_cap_eok"].includes(key)?Number(value):key==="max_positions"?Number(value):value}await api("/api/settings",{method:"PUT",body:JSON.stringify(next)});toast("설정을 저장했습니다");refresh()});
$("#start-button").addEventListener("click",async()=>{await api(state.engine.running?"/api/engine/stop":"/api/engine/start",{method:"POST"});refresh()});
$("#arm-button").addEventListener("click",async()=>{try{await api(state.engine.armed?"/api/engine/disarm":"/api/engine/arm",{method:"POST"});await refresh()}catch(error){toast(error.message)}});
async function refreshCandidates(){
  if(scanningCandidates)return;
  scanningCandidates=true;
  const button=$("#scan-button");button.disabled=true;button.textContent="전체 후보 조회 중…";
  try{
    displayCandidates=await api("/api/scan",{method:"POST"});
    renderCandidates(displayCandidates);refreshCandidateHistory();
    $("#scan-status").textContent=`${new Date().toLocaleTimeString("ko-KR")} 기준 · ${displayCandidates.length}개 · 조건 충족은 자동 주문 여부와 별개입니다`;
  }catch(error){
    $("#scan-status").textContent="조회 실패 · 이전 결과입니다";
    toast(error.message);
  }finally{scanningCandidates=false;button.disabled=false;button.textContent="지금 스캔"}
}
$("#scan-button").addEventListener("click",refreshCandidates);
refreshCandidates();
setInterval(refreshCandidates,30000);
setInterval(()=>{$("#clock").textContent=new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"})},1000);
window.addEventListener("resize",()=>state&&drawChart(state.trades));
refresh().catch(error=>toast(error.message));
let checkingEngine=false;
setInterval(async()=>{
  if(!state || checkingEngine)return;
  checkingEngine=true;
  try{
    state.engine=await api("/api/engine/status");
    renderEngine(state.engine);
  }catch(error){
    $("#status-dot").classList.remove("live");
    $("#engine-label").textContent="엔진 상태 확인 실패";
  }finally{checkingEngine=false}
},2000);

const historyMonth=$("#candidate-history-month");
const koreaToday=()=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Seoul"}).format(new Date());
historyMonth.value=koreaToday().slice(0,7);
const escapeText=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const historyCache=new Map();
let historyRequest=0;
function renderCandidateCalendar(month,days){
  const [year,monthNumber]=month.split("-").map(Number);
  const count=new Date(year,monthNumber,0).getDate(),offset=new Date(year,monthNumber-1,1).getDay(),today=koreaToday();
  let html=["일","월","화","수","목","금","토"].map(d=>`<div class="calendar-weekday">${d}</div>`).join("");
  for(let i=0;i<offset;i++)html+='<div class="calendar-day outside" aria-hidden="true"></div>';
  for(let day=1;day<=count;day++){
    const key=`${month}-${String(day).padStart(2,"0")}`,rows=days[key]||[];
    html+=`<div class="calendar-day ${key===today?'is-today':''}" aria-label="${key}"><span class="calendar-date">${day}</span>${rows.map(r=>`<button type="button" class="calendar-stock" data-chart-code="${escapeText(r.stock_code)}" data-chart-name="${escapeText(r.stock_name)}" data-chart-date="${key}" aria-label="${escapeText(r.stock_name)} 차트 보기"><span>${escapeText(r.stock_name)}</span><small>${Number(r.turnover_eok).toLocaleString("ko-KR",{maximumFractionDigits:1})}억</small></button>`).join("")}</div>`;
  }
  for(let i=(offset+count)%7;i>0&&i<7;i++)html+='<div class="calendar-day outside" aria-hidden="true"></div>';
  $("#candidate-calendar").innerHTML=html;
}
async function refreshCandidateHistory(){
  const month=historyMonth.value;if(!month)return;
  const request=++historyRequest,[year,monthNumber]=month.split("-").map(Number),count=new Date(year,monthNumber,0).getDate();
  const today=koreaToday(),cached=historyCache.get(month),days={...(cached||{})};
  renderCandidateCalendar(month,days);
  $("#history-status").textContent="조회 중…";
  try{
    const dates=Array.from({length:count},(_,i)=>`${month}-${String(i+1).padStart(2,"0")}`).filter(date=>date<=today&&(!cached||date===today||!(date in cached)));
    // 기존 날짜별 조회 API를 사용하되 동시 요청은 5개로 제한한다.
    for(let i=0;i<dates.length;i+=5){
      await Promise.all(dates.slice(i,i+5).map(async date=>{days[date]=await api(`/api/candidate-history?trade_date=${date}`)}));
      if(request!==historyRequest)return;
    }
    historyCache.set(month,days);
    if(request!==historyRequest||historyMonth.value!==month)return;
    renderCandidateCalendar(month,days);$("#history-status").textContent="";
  }catch(error){if(request===historyRequest)$("#history-status").textContent="기록을 불러오지 못했습니다. 잠시 후 다시 시도합니다."}
}
historyMonth.addEventListener("change",refreshCandidateHistory);
function moveHistoryMonth(amount){
  const [year,month]=historyMonth.value.split("-").map(Number),next=new Date(year,month-1+amount,1);
  historyMonth.value=`${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,"0")}`;refreshCandidateHistory();
}
$("#history-prev").addEventListener("click",()=>moveHistoryMonth(-1));
$("#history-next").addEventListener("click",()=>moveHistoryMonth(1));
refreshCandidateHistory();
setInterval(refreshCandidateHistory,30000);
let stockChartPoints=[];
let stockChartRequest=0;
const stockDialog=$("#stock-chart-dialog");
async function openStockChart(code,name,day){
  const request=++stockChartRequest;stockChartPoints=[];
  $("#stock-chart-title").textContent=name+" · "+code;
  $("#stock-chart-subtitle").textContent=day+" 기준 · 최근 60거래일 · 수정주가 일봉 · KRX";
  $("#stock-chart-detail").textContent="차트 조회 중…";
  if(!stockDialog.open)stockDialog.showModal();drawStockChart();
  try{
    const points=await api(`/api/stock-chart?code=${encodeURIComponent(code)}&base_date=${day}`);
    if(request!==stockChartRequest)return;
    stockChartPoints=points;drawStockChart();
    if(!points.length)$("#stock-chart-detail").textContent="해당 날짜까지의 차트 데이터가 없습니다.";
  }catch(error){if(request===stockChartRequest)$("#stock-chart-detail").textContent="차트 조회 실패 · "+error.message}
}
document.addEventListener("click",event=>{
  const button=event.target.closest("[data-chart-code]");if(!button)return;
  openStockChart(button.dataset.chartCode,button.dataset.chartName,button.dataset.chartDate||koreaToday());
});
$("#stock-chart-close").addEventListener("click",()=>stockDialog.close());
stockDialog.addEventListener("close",()=>{stockChartRequest++});
function drawStockChart(selected=null){
  if(!stockDialog.open)return;
  const canvas=$("#stock-chart"),ctx=canvas.getContext("2d"),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.scale(dpr,dpr);
  if(!stockChartPoints.length)return;
  const points=stockChartPoints,left=62,right=12,top=16,width=Math.max(1,rect.width-left-right),height=rect.height*.65,volumeTop=top+height+20,volumeHeight=rect.height-volumeTop-26;
  let low=Infinity,high=-Infinity,maxVolume=1;for(const p of points){low=Math.min(low,p.low);high=Math.max(high,p.high);maxVolume=Math.max(maxVolume,p.volume)}
  const margin=(high-low||high*.01)*.08;low-=margin;high+=margin;
  const step=width/points.length,x=i=>left+(i+.5)*step,y=v=>top+(high-v)/(high-low)*height;
  ctx.font="11px system-ui";ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const value=low+(high-low)*i/4,py=y(value);ctx.strokeStyle="#49354c";ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(left+width,py);ctx.stroke();
    ctx.fillStyle="#bba8bd";ctx.textAlign="right";ctx.fillText(Math.round(value).toLocaleString("ko-KR"),left-6,py+4);
  }
  points.forEach((p,i)=>{
    const color=p.close>=p.open?"#f3a6c8":"#8caefa",barWidth=Math.max(1,step*.65);
    ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(x(i),y(p.high));ctx.lineTo(x(i),y(p.low));ctx.stroke();
    ctx.fillRect(x(i)-barWidth/2,Math.min(y(p.open),y(p.close)),barWidth,Math.max(1,Math.abs(y(p.open)-y(p.close))));
    const v=p.volume/maxVolume*volumeHeight;ctx.globalAlpha=.6;ctx.fillRect(x(i)-barWidth/2,volumeTop+volumeHeight-v,barWidth,v);ctx.globalAlpha=1;
  });
  ctx.fillStyle="#bba8bd";ctx.textAlign="left";ctx.fillText(points[0].date,left,rect.height-6);ctx.textAlign="right";ctx.fillText(points.at(-1).date,left+width,rect.height-6);
  const index=selected===null?points.length-1:Math.max(0,Math.min(points.length-1,selected)),p=points[index];
  if(selected!==null){ctx.strokeStyle="#fff2f8";ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x(index),top);ctx.lineTo(x(index),volumeTop+volumeHeight);ctx.stroke();ctx.setLineDash([])}
  $("#stock-chart-detail").textContent=`${p.date} · 시가 ${money(p.open)} · 고가 ${money(p.high)} · 저가 ${money(p.low)} · 종가 ${money(p.close)} · 거래량 ${p.volume.toLocaleString()}주`;
}
$("#stock-chart").addEventListener("pointermove",event=>{const r=event.currentTarget.getBoundingClientRect();drawStockChart(Math.floor((event.clientX-r.left-62)/Math.max(1,r.width-74)*stockChartPoints.length))});
$("#stock-chart").addEventListener("pointerleave",()=>drawStockChart());
window.addEventListener("resize",()=>drawStockChart());
let orderStateLoading=false;
async function refreshOrderState(){
  if(orderStateLoading)return;orderStateLoading=true;
  try{
    const result=await api("/api/order-state");
    const held=result.positions.map(p=>`<tr><td>실제 보유</td><td>${escapeText(p.stock_name)}</td><td>${p.quantity}주</td><td>${money(p.quantity*p.average_price)}</td><td>매수 원금</td></tr>`);
    const pending=result.pending_entries.map(p=>{
      let note="체결 확인 대기";
      if(p.cancel_requested_at)note="취소 완료 확인 중";
      else note="IOC 즉시 체결 · 잔량 취소 확인 중";
      const quantity=p.remaining_quantity??p.quantity;
      return `<tr><td>${escapeText(p.status||"미체결")}</td><td>${escapeText(p.stock_name)}</td><td>${quantity}주</td><td>매수 대기</td><td>${note}</td></tr>`;
    });
    $("#order-state-body").innerHTML=[...held,...pending].join("")||'<tr><td colspan="5" class="empty-cell">보유 및 매수 대기 내역이 없습니다</td></tr>';
    $("#order-state-note").textContent="실제 체결 수량만 보유로 표시 · IOC 매수의 미체결 잔량은 즉시 취소";
  }catch(error){$("#order-state-note").textContent="보유·미체결 조회 실패 · "+error.message}
  finally{orderStateLoading=false}
}
refreshOrderState();
setInterval(refreshOrderState,3000);
let refreshingTrades=false;
setInterval(async()=>{
  if(refreshingTrades||!state)return;refreshingTrades=true;
  try{
    const latest=await api("/api/dashboard");
    state={...state,trades:latest.trades,today:latest.today,recent5:latest.recent5,recent10:latest.recent10,recent20:latest.recent20};
    renderTrades(state.trades);renderPerformance();
    $("#today-count").textContent=state.today.count;$("#today-winrate").textContent=pct(state.today.win_rate);
    $("#today-expectancy").textContent=pct(state.today.expectancy);$("#today-pnl").textContent=money(state.today.cumulative_pnl);
  }catch(error){toast("거래 기록 갱신 실패 · "+error.message)}finally{refreshingTrades=false}
},5000);