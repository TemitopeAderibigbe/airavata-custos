# Custos-protected DimeNet++ inference gateway (proof of concept)

Research question: can Apache Airavata Custos control who is allowed to run a scientific
foundation model's inference, and what does it take to connect a model to Custos authentication?

The gateway puts Custos's Keycloak sign-in and a role check in front of **DimeNet++**
(PyTorch Geometric, pretrained on QM9, target 7 = U0). DimeNet++ inference is taken from the
[DimeNet++ recipe](https://github.com/cyber-shuttle/cybershuttle-ai4science-foundry/pull/47)
in the CyberShuttle AI4Science Foundry.

```
user --(Keycloak token)--> gateway :8090 --checks--> signature, issuer, expiry, client, role
                                         --if allowed--> DimeNet++ on one QM9 molecule --> prediction
                                         --always-->     audit log (/data/audit.log)
```

## Requirements

- Docker Desktop
- The local Custos stack from [INSTALL.md](../../INSTALL.md) with Postgres and Keycloak running
  (`docker compose up -d db keycloak` in `dev-ops/compose`). The gateway joins its Docker network,
  `compose_default`.
- Internet access on first run: the image build pulls about 2.5 GB of Python packages, and the
  first start downloads QM9 and the pretrained DimeNet++ checkpoint into the `dimenet-data` volume.

## Steps

1. **Configure Keycloak** (once). Creates a client for the gateway that allows password logins,
   a realm role `dimenet-user`, and gives that role to the dev user `pi`:

   ```
   docker exec custos_keycloak /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user admin --password admin
   docker exec custos_keycloak /opt/keycloak/bin/kcadm.sh create clients -r custos -s clientId=dimenet-gateway -s publicClient=true -s directAccessGrantsEnabled=true -s standardFlowEnabled=false -s enabled=true
   docker exec custos_keycloak /opt/keycloak/bin/kcadm.sh create roles -r custos -s name=dimenet-user
   docker exec custos_keycloak /opt/keycloak/bin/kcadm.sh add-roles -r custos --uusername pi --rolename dimenet-user
   ```

2. **Build and start the gateway** (from this folder):

   ```
   docker build -t custos-dimenet-gateway .
   docker run -d --name dimenet-gateway --network compose_default -p 127.0.0.1:8090:8090 -v dimenet-data:/data -e CUSTOS_ISSUER=http://keycloak:8080/realms/custos custos-dimenet-gateway
   docker logs -f dimenet-gateway
   ```

   Wait for `model loaded in ...` (about 20 s once QM9 and the checkpoint are cached).

   `CUSTOS_ISSUER` must match the `iss` claim in the tokens. `run_experiments.py` gets tokens from
   inside Docker (`http://keycloak:8080`), so Keycloak stamps that address as the issuer. Tokens
   requested from the host through `http://localhost:8081` carry `http://localhost:8081/realms/custos`
   instead, which is the default.

3. **Run the experiments:**

   ```
   docker exec dimenet-gateway python run_experiments.py
   ```

   Expected: no token returns 401, `researcher` returns 403, `pi` returns 200 with a prediction,
   and a tampered token returns 401. Then ten authorized predictions with their error and latency.

4. **Audit log:** `docker exec dimenet-gateway cat /data/audit.log`

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `CUSTOS_ISSUER` | `http://localhost:8081/realms/custos` | Expected `iss` claim |
| `CUSTOS_JWKS_URL` | `http://keycloak:8080/realms/custos/protocol/openid-connect/certs` | Keycloak signing keys |
| `GATEWAY_CLIENT_ID` | `dimenet-gateway` | Required `azp` claim |
| `REQUIRED_ROLE` | `dimenet-user` | Realm role needed to run inference |
| `DATA_DIR` | `/data` | QM9, checkpoint cache, audit log |

## Dependency notes

PyG does not install these automatically. Each was found by a failed run. TensorFlow is also
required, because `from_qm9_pretrained` converts the original TensorFlow DimeNet++ checkpoint.

| Symptom | Cause | Fix in Dockerfile |
| --- | --- | --- |
| `AttributeError: module 'numpy' has no attribute 'math'` | PyG 2.6.1 `dimenet_utils.py` uses `np.math`, removed in NumPy 2 | `numpy==1.26.4` |
| `ImportError: 'SparseTensor' requires 'torch-sparse'` | DimeNet++ builds bond-angle triplets with `SparseTensor` | `torch-sparse==0.6.18` |
| `ModuleNotFoundError: No module named 'torch_scatter'` | `torch_sparse` imports `torch_scatter` | `torch-scatter==2.1.2` |

## Known limitations

- Roles are Keycloak realm roles, not Custos allocations. Checking a user's Custos project
  allocation before each run is the next step.
- The `dimenet-gateway` client allows password logins, which is fine for local testing only.
- Runs locally on CPU, not on CyberShuttle or PACE ICE.
