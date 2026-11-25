import sqlite3, json
keys = ["gpm","vpm","kp","deaths","dmg_share"]
aggregates = ", ".join(f"avg(json_extract(stats,'$.{k}')), count(json_extract(stats,'$.{k}'))" for k in keys)
result = {}
for tier, role, n, *v in sqlite3.connect("meta.sqlite").execute(f"SELECT tier, role, count(distinct match_id), {aggregates} FROM games WHERE stats IS NOT NULL GROUP BY tier,role HAVING count(distinct match_id)>=30"):
    if tier and role:
        result.setdefault(tier,{})[role] = {"n": n, **{k:round(v[i*2],3) for i,k in enumerate(keys) if v[i*2+1]>=30}}
assert result
with open("radar-tiers.json","w") as out:
    json.dump(result,out)
