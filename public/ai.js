const msgs=document.getElementById("msgs"),q=document.getElementById("q");
function say(t,c){const d=document.createElement("div");d.className="m "+c;d.textContent=t;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d}
async function ask(text){
 if(!text.trim())return;say(text,"me");q.value="";const w=say("Thinking…","bot");
 try{const r=await fetch("/ai",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF":document.body.dataset.csrf},body:JSON.stringify({q:text})});
  const j=await r.json();w.textContent=j.message;
  j.results.forEach(v=>{const a=document.createElement("a");a.className="m rec";a.href=v.kind==="Car"?"/cars":"/bikes";
   const n=document.createElement("b");n.textContent=v.name+" · ₹"+v.price.toLocaleString("en-IN")+"/day";
   const s=document.createElement("span");s.textContent=v.why;a.append(n,s);msgs.appendChild(a)});
 }catch(e){w.textContent="Something went wrong. Please try again."}
}
document.getElementById("aiform").onsubmit=e=>{e.preventDefault();ask(q.value)};
document.querySelectorAll(".sugg button").forEach(b=>b.onclick=()=>ask(b.textContent));
