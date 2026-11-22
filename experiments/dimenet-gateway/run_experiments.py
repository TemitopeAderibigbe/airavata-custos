# Licensed to the Apache Software Foundation (ASF) under the Apache License, Version 2.0.
# See gateway.py for the full license header.
"""Run the access-control experiments against the gateway and print the results.

Run inside the gateway container:  docker exec dimenet-gateway python run_experiments.py
"""
import json
import os
import statistics

import requests

KC = os.environ.get("KC_TOKEN_URL", "http://keycloak:8080/realms/custos/protocol/openid-connect/token")
GW = "http://localhost:8090/predict"
CLIENT = os.environ.get("GATEWAY_CLIENT_ID", "dimenet-gateway")


def token(user):
    r = requests.post(KC, data={"grant_type": "password", "client_id": CLIENT,
                                "username": f"{user}@custos.local", "password": user}, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def call(label, headers, index=0):
    r = requests.post(GW, json={"index": index}, headers=headers, timeout=120)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    print(f"{label:<34} HTTP {r.status_code}  {json.dumps(body)}")
    return r.status_code, body


print("== Experiment 1: access control ==")
pi, researcher = token("pi"), token("researcher")
results = [
    ("1. no token", *call("1. no token", {})),
    ("2. researcher (no dimenet-user role)", *call("2. researcher (no role)", {"Authorization": f"Bearer {researcher}"})),
    ("3. pi (has dimenet-user role)", *call("3. pi (has role)", {"Authorization": f"Bearer {pi}"})),
    ("4. pi token, signature tampered", *call("4. pi token, tampered", {"Authorization": f"Bearer {pi[:-4]}AAAA"})),
]

print("\n== Experiment 2: authorized inference on QM9 test molecules 0-9 ==")
errors, latencies = [], []
for i in range(10):
    code, body = call(f"pi, molecule {i}", {"Authorization": f"Bearer {pi}"}, index=i)
    if code == 200:
        errors.append(body["abs_error_eV"])
        latencies.append(body["latency_ms"])
if errors:
    print(f"\nMAE over {len(errors)} molecules: {statistics.mean(errors):.4f} eV "
          f"({statistics.mean(errors) * 1000:.1f} meV)")
    print(f"Latency per request: median {statistics.median(latencies):.1f} ms, "
          f"max {max(latencies):.1f} ms (first call includes warm-up)")
