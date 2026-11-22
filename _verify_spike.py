import model
f = model.predict_next_24h(False); s = model.predict_next_24h(True)
for h,(a,b) in enumerate(zip(f,s)):
    if abs(a-b) > 1e-9:
        print(f"{h:02d}:00  base={a:8.4f}  spike={b:8.4f}  delta={b-a:+.4f}")
print("hour19 delta exact:", round(s[19]-f[19], 6))
print("sum delta:", round(sum(s)-sum(f), 6))
