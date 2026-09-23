async function refreshStatus(){
  try{
    const r=await fetch("/api/status",{cache:"no-store"}); const d=await r.json();
    let active=0, mem=0, cpu=0, storage=0;
    d.bots.forEach(b=>{
      if(b.running) active++;
      mem+=b.memory||0; cpu+=b.cpu||0; storage+=b.storage||0;
      const card=document.querySelector(`[data-bot-id="${b.id}"]`);
      if(card){
        card.querySelector(".cpu").textContent=(b.cpu||0)+"%";
        card.querySelector(".memory").textContent=(b.memory||0)+" MB";
        card.querySelector(".pid").textContent=b.pid||"—";
      }
    });
    const a=document.getElementById("activeCount"),m=document.getElementById("memoryTotal"),c=document.getElementById("cpuTotal"),s=document.getElementById("storageTotal");
    if(a)a.textContent=active;if(m)m.textContent=mem.toFixed(1)+" MB";if(c)c.textContent=cpu.toFixed(1)+"%";if(s)s.textContent=storage.toFixed(2)+" MB";
  }catch(e){}
}
setInterval(refreshStatus,3000); refreshStatus();

async function showLogs(id,name){
  document.getElementById("logTitle").textContent=name+" · Console";
  document.getElementById("logBody").textContent="Loading…";
  document.getElementById("logModal").hidden=false;
  const load=async()=>{try{let r=await fetch("/api/bot/"+id+"/logs",{cache:"no-store"});let d=await r.json();let el=document.getElementById("logBody");el.textContent=d.logs||"No logs.";el.scrollTop=el.scrollHeight}catch(e){}};
  await load();
  window._logTimer=setInterval(load,2000);
}
function closeLogs(){document.getElementById("logModal").hidden=true;clearInterval(window._logTimer)}
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeLogs()});
function switchEditor(which,btn){
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));btn.classList.add("active");
  document.getElementById("codePane").hidden=which!=="code";document.getElementById("reqPane").hidden=which!=="req";
}
