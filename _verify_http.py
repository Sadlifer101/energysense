import json, urllib.request, urllib.error
B = "http://127.0.0.1:5000"
def get(path):
    try:
        with urllib.request.urlopen(B+path, timeout=30) as r:
            raw = r.read()
            try: return r.status, raw.decode("utf-8")
            except UnicodeDecodeError: return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
def post(path, body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(B+path, data=data, headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e: return e.code, e.read().decode("utf-8", "replace")

ok = True
def chk(n,c,d=""):
    global ok; ok = ok and bool(c); print(("PASS " if c else "FAIL ")+n+(" :: "+str(d) if d else ""))

st, body = get("/"); chk("GET / 200", st==200, st); chk("GET / landing markup", "EnergySense" in body, len(body))
chk("GET / css", "/static/landing/landing.css" in body)
st, body = get("/app"); chk("GET /app 200", st==200, st); chk("GET /app markup", "dashboard" in body.lower(), len(body))
chk("GET /app chartjs local", "/static/vendor/chartjs/chart.umd.js" in body)
for asset in ["/static/landing/landing.js","/static/landing/landing.css","/static/dashboard/dashboard.js","/static/dashboard/dashboard.css","/static/vendor/chartjs/chart.umd.js","/static/landing/img/hero-stone.webp","/static/landing/img/hero-drape.webp","/static/landing/fonts/fraunces-latin-var.woff2","/static/landing/fonts/dm-sans-latin.woff2","/static/landing/img/arch-olive.webp","/static/landing/img/bronze-disc.webp","/static/landing/img/dusk-ridge.webp","/static/landing/img/hero-branch.webp","/static/landing/img/hero-pedestal.webp","/static/landing/img/hero-wall.webp"]:
    st, payload = get(asset); chk(f"asset {asset.split('/')[-1]}", st==200 and (len(payload)>0), st)

KEYS = ["meta","model","hours","baseline_kwh","optimized_kwh","tariff_inr_per_kwh","carbon_kg_per_kwh","savings","actuators","agent_logs","appliance_estimates","assumptions"]
st, body = get("/api/dashboard?city=Bengaluru&spike=false"); d = json.loads(body)
chk("dashboard 200", st==200, st)
chk("top-level keys exact+ordered", list(d.keys())==KEYS, list(d.keys()))
for k in ["hours","baseline_kwh","optimized_kwh","tariff_inr_per_kwh","carbon_kg_per_kwh"]:
    chk(f"{k} len 24", len(d[k])==24, len(d[k]))
e0=sum(d["baseline_kwh"]); e1=sum(d["optimized_kwh"])
chk("energy conserved in API", abs(e1-e0)<=0.005*e0, f"{e0:.4f}->{e1:.4f}")
cost0=round(sum(a*b for a,b in zip(d["baseline_kwh"],d["tariff_inr_per_kwh"])),2)
chk("baseline cost matches tariff math", abs(cost0-d["savings"]["daily_baseline_cost_inr"])<0.02, (cost0, d["savings"]["daily_baseline_cost_inr"]))
chk("savings = base-opt cost", abs((d["savings"]["daily_baseline_cost_inr"]-d["savings"]["daily_optimized_cost_inr"])-d["savings"]["daily_inr"])<0.02, d["savings"]["daily_inr"])
chk("monthly = 30x daily", abs(d["savings"]["monthly_inr"]-d["savings"]["daily_inr"]*30)<0.5, d["savings"]["monthly_inr"])
chk("no NaN in series", all(isinstance(v,(int,float)) and v==v for v in d["baseline_kwh"]+d["optimized_kwh"]))
chk("actuators present", len(d["actuators"])>0, len(d["actuators"]))
chk("agent_logs present", len(d["agent_logs"])>0, len(d["agent_logs"]))
chk("appliance estimates present", len(d["appliance_estimates"])>0, len(d["appliance_estimates"]))
print("   model:", d["model"]); print("   savings:", d["savings"])
print("   actuators:", [a.get("name") or a.get("appliance") or a.get("id") for a in d["actuators"]])
print("   log sample:", d["agent_logs"][0])

seen = {}
for c in ["Bengaluru","Mumbai","Delhi"]:
    st, body = get(f"/api/dashboard?city={c}&spike=false"); dd=json.loads(body)
    seen[c]=(dd["meta"]["city"], dd["tariff_inr_per_kwh"][18], dd["savings"]["daily_inr"], len(dd["agent_logs"]))
    chk(f"city {c} ok", st==200 and dd["meta"]["city"]==c, st)
chk("city tariffs differ", len({round(v[1],2) for v in seen.values()})==3, seen)
st, body = get("/api/dashboard?city=bogus"); chk("bad city -> 400 JSON", st==400 and "error" in json.loads(body), body[:100])
st, body = get("/api/dashboard?spike=maybe"); chk("bad bool -> 400", st==400, st)

st, body = get("/api/dashboard?city=Bengaluru&spike=true"); ds=json.loads(body)
chk("spike GET 200", st==200, st)
chk("spike meta scenario", ds["meta"]["scenario"]=="spike", ds["meta"])
chk("spike raises 19:00 baseline", ds["baseline_kwh"][19] > d["baseline_kwh"][19]+3.4, (d["baseline_kwh"][19], ds["baseline_kwh"][19]))
chk("spike changes actuators", json.dumps(ds["actuators"]) != json.dumps(d["actuators"]))
chk("spike changes logs", json.dumps(ds["agent_logs"]) != json.dumps(d["agent_logs"]))
chk("spike changes savings", ds["savings"] != d["savings"], ds["savings"]["daily_inr"])
chk("spike conserved", abs(sum(ds["optimized_kwh"])-sum(ds["baseline_kwh"]))<=0.005*sum(ds["baseline_kwh"]), ds["savings"]["daily_inr"])
print("   spike savings:", ds["savings"])

st, body = post("/api/simulate-spike", {"city":"Mumbai","enabled":True}); dp=json.loads(body)
chk("POST spike 200", st==200, st)
chk("POST spike city+scenario", dp["meta"]["city"]=="Mumbai" and dp["meta"]["scenario"]=="spike", dp["meta"])
st, body = post("/api/simulate-spike", {"city":"Mumbai","enabled":False})
chk("POST disabled -> normal", st==200 and json.loads(body)["meta"]["scenario"]=="normal", st)
st, body = post("/api/simulate-spike", {"city":"Atlantis","enabled":True})
chk("POST bad city -> 400", st==400 and "error" in json.loads(body), st)
st, body = post("/api/simulate-spike", {})
chk("POST empty body -> 200 normal", st==200, st)
st, body = post("/api/simulate-spike", b"not json", raw=True)
chk("POST malformed -> 400", st==400, st)
st, body = get("/nope"); chk("404 -> HTML (non-api)", st==404, st)
st, body = get("/api/nope"); chk("api 404 -> JSON", st==404 and "error" in body, body[:80])
print("\nALL HTTP CHECKS PASS" if ok else "\nSOME HTTP CHECKS FAILED")
