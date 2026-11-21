# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Custos-protected inference gateway for DimeNet++ (QM9, target 7).

Every request must carry a Keycloak access token from the Custos realm.
The gateway checks the token signature, issuer, expiry and issuing client,
then checks that the user holds the required realm role before running
DimeNet++ on one QM9 test molecule. Every decision is written to an audit log.
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import jwt
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

ISSUER = os.environ.get("CUSTOS_ISSUER", "http://localhost:8081/realms/custos")
JWKS_URL = os.environ.get(
    "CUSTOS_JWKS_URL",
    "http://keycloak:8080/realms/custos/protocol/openid-connect/certs")
CLIENT_ID = os.environ.get("GATEWAY_CLIENT_ID", "dimenet-gateway")
REQUIRED_ROLE = os.environ.get("REQUIRED_ROLE", "dimenet-user")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
TARGET = 7  # U0 after PyG's pretrained-target remap (atomization energy, eV)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")

state = {"model": None, "test_dataset": None, "load_error": None, "load_seconds": None}
_jwks_client = None


def load_model():
    """Load QM9 and the pretrained DimeNet++ checkpoint (partner's snippet)."""
    import torch
    from torch_geometric.datasets import QM9
    from torch_geometric.nn import DimeNetPlusPlus

    root = os.path.join(DATA_DIR, "qm9")
    dataset = QM9(root=root)
    # PyG's pretrained DimeNet++ maps atomization targets [7,8,9,10] -> [12,13,14,15]
    idx = torch.tensor([0, 1, 2, 3, 4, 5, 6, 12, 13, 14, 15, 11])
    dataset._data.y = dataset._data.y[:, idx]
    model, (_, _, test_dataset) = DimeNetPlusPlus.from_qm9_pretrained(
        root=root, dataset=dataset, target=TARGET)
    return model.eval(), test_dataset


@asynccontextmanager
async def lifespan(app):
    start = time.time()
    try:
        state["model"], state["test_dataset"] = load_model()
        state["load_seconds"] = round(time.time() - start, 1)
        log.info("model loaded in %ss, %d test molecules",
                 state["load_seconds"], len(state["test_dataset"]))
    except Exception as exc:  # keep serving so the failure is visible
        state["load_error"] = f"{type(exc).__name__}: {exc}"
        log.exception("model failed to load")
    yield


app = FastAPI(title="Custos DimeNet++ gateway", lifespan=lifespan)


def audit(**fields):
    fields["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = json.dumps(fields)
    log.info("AUDIT %s", line)
    try:
        with open(os.path.join(DATA_DIR, "audit.log"), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def verify(authorization: Optional[str]) -> dict:
    """Return the token claims, or raise 401/403."""
    global _jwks_client
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(JWKS_URL)
        key = _jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER,
                            options={"verify_aud": False, "require": ["exp", "iss"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {type(exc).__name__}: {exc}")
    if claims.get("azp") != CLIENT_ID:
        raise HTTPException(401, f"token was issued to client '{claims.get('azp')}', not '{CLIENT_ID}'")
    roles = claims.get("realm_access", {}).get("roles", [])
    if REQUIRED_ROLE not in roles:
        raise HTTPException(403, f"user '{claims.get('preferred_username')}' lacks role '{REQUIRED_ROLE}'")
    return claims


class PredictRequest(BaseModel):
    index: int = 0


@app.get("/health")
def health():
    return {"model_loaded": state["model"] is not None,
            "load_seconds": state["load_seconds"],
            "load_error": state["load_error"]}


@app.post("/predict")
def predict(req: PredictRequest, authorization: Optional[str] = Header(None)):
    start = time.time()
    try:
        claims = verify(authorization)
    except HTTPException as exc:
        audit(decision="deny", status=exc.status_code, reason=exc.detail, index=req.index)
        raise
    user = claims.get("preferred_username")
    if state["model"] is None:
        audit(decision="error", status=503, user=user, reason=state["load_error"], index=req.index)
        raise HTTPException(503, f"model not loaded: {state['load_error']}")
    ds = state["test_dataset"]
    if not 0 <= req.index < len(ds):
        raise HTTPException(400, f"index must be between 0 and {len(ds) - 1}")

    import torch
    mol = ds[req.index]
    with torch.no_grad():
        pred = state["model"](mol.z, mol.pos, batch=None).item()
    actual = mol.y[0, TARGET].item()
    ms = round((time.time() - start) * 1000, 1)
    audit(decision="allow", status=200, user=user, index=req.index,
          predicted=round(pred, 4), actual=round(actual, 4), latency_ms=ms)
    return {"user": user, "molecule_index": req.index, "num_atoms": int(mol.z.numel()),
            "predicted_U0_eV": round(pred, 4), "actual_U0_eV": round(actual, 4),
            "abs_error_eV": round(abs(pred - actual), 4), "latency_ms": ms}
