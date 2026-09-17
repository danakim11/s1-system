const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let state = null;
let activeWindow = "recent10";

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
  $("#status-dot").classList.toggle("live",engine.running); $("#engine-label").textContent=engine.running?"엔진 실행 중":"엔진 정지";
  $("#start-button").textContent=engine.running?"엔진 정지":"엔진 시작";
  $("#execution-title").textContent=engine.armed?"실주문 활성":"안전 잠금";
  $("#execution-detail").textContent=engine.armed?"조건 충족 시 주문 전송":engine.live_enabled?"실주문 대기":"운영 주문 비활성";
  $("#arm-button").textContent=engine.armed?"실주문 잠금":"실주문 활성화";
  Object.entries(settings).forEach(([key,value])=>{const input=$(`[name="${key}"]`);if(input)input.value=value});
  renderPerformance(); renderTrades(trades); renderCandidates(engine.last_scan||[]);
}
function renderPerformance(){
  const perf=state[activeWindow];
  $("#recent-winrate").textContent=pct(perf.win_rate); $("#recent-win").textContent=pct(perf.average_win);
  $("#recent-loss").textContent=pct(perf.average_loss); $("#recent-expectancy").textContent=pct(perf.expectancy);
  drawChart(state.trades);
}
function drawChart(trades){
  const canvas=$("#equity-chart"), ctx=canvas.getContext("2d"), dpr=window.devicePixelRatio||1, rect=canvas.getBoundingClientRect();
  canvas.width=rect.width*dpr; canvas.height=rect.height*dpr; ctx.scale(dpr,dpr); ctx.clearRect(0,0,rect.width,rect.height);
  const series=[0]; trades.forEach(t=>series.push(series.at(-1)+Number(t.realized_pnl)));
  if(series.length<2){ctx.fillStyle="#606a77";ctx.font="13px system-ui";ctx.fillText("거래 후 누적 손익 곡선이 표시됩니다",12,rect.height/2);return}
  const min=Math.min(...series),max=Math.max(...series),range=max-min||1,pad=12;
  ctx.beginPath();series.forEach((v,i)=>{const x=pad+i*(rect.width-pad*2)/(series.length-1),y=rect.height-pad-(v-min)*(rect.height-pad*2)/range;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});
  ctx.strokeStyle="#c9ff3d";ctx.lineWidth=2.4;ctx.stroke();
}
function renderTrades(trades){
  const body=$("#trade-body"); if(!trades.length){body.innerHTML='<tr><td colspan="7" class="empty-cell">아직 기록된 거래가 없습니다</td></tr>';return}
  body.innerHTML=[...trades].reverse().slice(0,20).map(t=>`<tr><td>${new Date(t.exited_at).toLocaleDateString("ko-KR")}</td><td><b>${t.stock_name}</b><br><small>${t.stock_code}</small></td><td>${t.level}%</td><td>-${t.stop_loss_rate}%</td><td><span class="badge">${t.exit_reason}</span></td><td class="${t.return_pct>=0?'positive':'negative'}">${pct(t.return_pct)}</td><td>${money(t.realized_pnl)}</td></tr>`).join("");
}
function renderCandidates(items){
  const body=$("#candidate-body");if(!items.length){body.innerHTML='<tr><td colspan="6" class="empty-cell">키움 연결 후 스캔할 수 있습니다</td></tr>';return}
  body.innerHTML=items.map(c=>`<tr><td><b>${c.stock_name}</b><br><small>${c.stock_code}</small></td><td>${money(c.price).replace('원','')}</td><td class="positive">+${pct(c.change_rate)}</td><td>${c.turnover_eok.toLocaleString()}억</td><td>${c.market_cap_eok.toLocaleString()}억</td><td><span class="badge ${c.allowed?'allowed':''}">${c.allowed?'진입 가능':'관찰'}</span></td></tr>`).join("");
}

$$("[data-level]").forEach(button=>button.addEventListener("click",async()=>{const next={...state.settings,applied_level:Number(button.dataset.level)};await api("/api/settings",{method:"PUT",body:JSON.stringify(next)});toast(`${next.applied_level}% 적용`);refresh()}));
$$("[data-window]").forEach(button=>button.addEventListener("click",()=>{activeWindow=button.dataset.window;$$('[data-window]').forEach(b=>b.classList.toggle('active',b===button));renderPerformance()}));
$("#save-button").addEventListener("click",async()=>{const form=new FormData($("#settings-form"));const next={...state.settings};for(const [key,value] of form){next[key]=["entry_rate","stop_loss_rate","min_turnover_eok","min_market_cap_eok"].includes(key)?Number(value):key==="max_positions"?Number(value):value}await api("/api/settings",{method:"PUT",body:JSON.stringify(next)});toast("설정을 저장했습니다");refresh()});
$("#start-button").addEventListener("click",async()=>{await api(state.engine.running?"/api/engine/stop":"/api/engine/start",{method:"POST"});refresh()});
$("#arm-button").addEventListener("click",async()=>{if(state.engine.armed){await api("/api/engine/disarm",{method:"POST"});refresh()}else $("#arm-dialog").showModal()});
$("#confirm-arm").addEventListener("click",async(event)=>{event.preventDefault();try{await api("/api/engine/arm",{method:"POST",body:JSON.stringify({phrase:$("#arm-phrase").value})});$("#arm-dialog").close();$("#arm-phrase").value="";refresh()}catch(error){toast(error.message)}});
$("#scan-button").addEventListener("click",async()=>{try{const items=await api("/api/scan",{method:"POST"});renderCandidates(items);toast(`${items.length}개 후보를 확인했습니다`)}catch(error){toast(error.message)}});
setInterval(()=>{$("#clock").textContent=new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"})},1000);
window.addEventListener("resize",()=>state&&drawChart(state.trades));
refresh().catch(error=>toast(error.message));

