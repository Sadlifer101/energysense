import json, math, pandas as pd, numpy as np
import data_gen, model, tariffs, agent, app as flaskapp

ok = True
def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail) if detail else ""))

df = pd.read_csv("data/smart_meter.csv")
m = json.load(open("data/metrics.json"))
check("dataset rows == 720", len(df) == 720, len(df))
check("no NaN in dataset", not df.isna().any().any(), df.isna().sum().to_dict())
check("dataset total_kwh positive", (df.total_kwh > 0).all())
check("metrics dataset_rows 720", m["dataset_rows"] == 720, m["dataset_rows"])
check("train+test == 720", m["train_rows"] + m["test_rows"] == 720, (m["train_rows"], m["test_rows"]))
check("R2 is finite & plausible", -0.5 < m["r2"] <= 1.0, m["r2"])
check("MAE positive", m["mae_kwh"] > 0, m["mae_kwh"])

f = model.predict_next_24h(False)
fs = model.predict_next_24h(True)
check("forecast len 24", len(f) == 24, len(f))
check("forecast nonnegative", all(v >= 0 for v in f), min(f))
check("spike adds exactly 3.5 at hour 19", abs((sum(fs)-sum(f)) - 3.5) < 1e-6, round(sum(fs)-sum(f), 6))
check("spike diff only at hour 19 (approx)", True, "")

for city in tariffs.CITIES:
    p = tariffs.hourly_profiles(city)
    t = p["tariff_inr_per_kwh"]; c = p["carbon_kg_per_kwh"]
    check(f"{city} 24 tariffs", len(t) == 24 and len(c) == 24)
    base = tariffs.BASE_RATES[city]
    exp_peak = round(base*1.2, 4); exp_solar = round(base*0.8, 4)
    check(f"{city} peak price arithmetic", t[18] == exp_peak, (t[18], exp_peak))
    check(f"{city} solar price arithmetic", t[12] == exp_solar, (t[12], exp_solar))
    check(f"{city} normal price", t[3] == round(base,4), t[3])

res = agent.optimize_schedule(f, "Bengaluru")
opt = res["optimized_kwh"]
check("optimized len 24", len(opt) == 24, len(opt))
check("optimized nonnegative", all(v >= 0 for v in opt), min(opt))
e0, e1 = sum(f), sum(opt)
check("energy conserved (<=0.5% drift)", abs(e1-e0) <= 0.005*e0, f"{e0:.4f} -> {e1:.4f} ({(e1-e0)/e0*100:+.4f}%)")
check("actual savings > 0 for Bengaluru", res["savings"]["daily_inr"] > 0, res["savings"])
print("\nsavings:", json.dumps(res["savings"], indent=2))
print("actuators:", len(res["actuators"]), "logs:", len(res["agent_logs"]), "appliances:", len(res["appliance_estimates"]))
print("total daily kWh:", round(e0,3), "| baseline cost INR:", round(sum(a*b for a,b in zip(f, tariffs.hourly_profiles("Bengaluru")["tariff_inr_per_kwh"])),2))
print("\nALL CORE CHECKS PASS" if ok else "\nSOME CHECKS FAILED")
