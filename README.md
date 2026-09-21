# lfi-service-1-app

The gateway service for a local GitOps Kubernetes platform. FastAPI, Python 3.12.

**Companion to a build guide.** This is the reference version of what you build in
Section 3 of [local-full-infrastructure](https://github.com/mhonnczedz1/local-full-infrastructure).
Read the guide for why it is shaped this way; clone this only to compare against your own.

## What it does

Receives external HTTP through the Traefik ingress, calls `service-2` over cluster DNS,
and persists the result to Postgres.

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness. Touches nothing, so a database blip never kills a healthy pod. |
| `GET /readyz` | Readiness. Touches Postgres, so a pod that cannot reach it leaves the Service endpoints. |
| `POST /compute` | Calls service-2, then writes. Worker call first, so a failed upstream leaves no row. |
| `GET /history` | The ten most recent rows. |

The liveness and readiness split is the design decision worth noticing. A pod that has
lost its database drops out of service without being restarted, and rejoins on its own
once Postgres returns.

## What it deliberately does not contain

No Kubernetes manifests, no cluster credentials, no knowledge of where it is deployed.
CI builds an image, pushes it to GHCR, and writes one tag into the
[GitOps repo](https://github.com/mhonnczedz1/lfi-infrastructure-gitops). That narrowness
is the architecture, not an omission: the entire cluster could be replaced without
touching this repository.

## Running the tests

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v && ruff check src tests
```

The suite needs no database and no service-2. Both are replaced through FastAPI
dependency overrides and an httpx mock transport, which is why CI runs it with no
service containers at all.

## CI

`test` runs lint and tests on every push and pull request. `build` publishes a
multi-arch image to GHCR, tagged with both a weekly build ordinal and the commit SHA,
and is skipped on pull requests. `promote` writes the build tag into the GitOps repo's
**dev** overlay. Nothing here ever writes to prod.
