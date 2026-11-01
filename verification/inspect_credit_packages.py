"""Inspect only bounded non-secret resource-package field names."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine

client = engine.Engine()
now = client.now()
client._bind(now.date().isoformat())
code, body = client._request("POST", "/billing/meter/get-user-resource-summary", {})
packages = engine._data(body).get("Packages", [])
body = {"PageNumber": 1, "PageSize": 100, "PackageCodes": [p["PackageCode"] for p in packages], "Status": json.loads(sys.argv[1]), "NeedRenewInfo": True}
for kind in ("paid", "free"):
    payload = dict(body)
    if kind == "free":
        payload.update(SlicePeriodStartTime=now.strftime("%Y-%m-%d 00:00:00"), SlicePeriodEndTime=now.strftime("%Y-%m-%d 23:59:59"))
    code, response = client._request("POST", f"/billing/meter/get-user-resource-{kind}-packages", payload)
    data = engine._data(response)
    accounts = data.get("Accounts")
    print(json.dumps({"kind": kind, "http": code, "business_code": response.get("code") if isinstance(response, dict) else None,
                      "data_keys": sorted(data), "count": data.get("TotalCount"),
                      "account_keys": [sorted(a) for a in accounts[:3]] if isinstance(accounts, list) else None,
                      "expiry_fields": [{k: v for k, v in a.items() if any(s in k.lower() for s in ("time", "capacity", "status", "name", "code")) and isinstance(v, (str, int, float, type(None)))} for a in accounts[:8]] if isinstance(accounts, list) else None}, ensure_ascii=True))
