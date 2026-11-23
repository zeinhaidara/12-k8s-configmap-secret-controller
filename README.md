# ConfigMap-to-Secret sync controller

Apps often need secrets from a cloud secret store. Copying those values into Kubernetes by
hand means they end up pasted into terminals and chat logs, which leaks them. And once
copied, a manual Secret goes stale the moment the value rotates in the store.

This controller watches ConfigMaps for a sync annotation and keeps a matching Secret in
step with the named entry in the secret store.

## Flow

```mermaid
flowchart LR
    A[ConfigMap annotation] --> B[controller]
    B --> C[secret store]
    C --> B
    B --> D[Kubernetes Secret]
```

## Usage

Annotate a ConfigMap with the name of a secret in AWS Secrets Manager. The controller creates or
updates a Secret named `<configmap>-synced` in the same namespace, owned by the ConfigMap, with the
secret value under a key of that same name. It never overwrites a Secret it did not create.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: payments
  annotations:
    secret-sync.example.com/source: prod/payments/db-password
```

The controller only runs inside a cluster (`load_incluster_config`), with `NAMESPACE` set and AWS
credentials from its service account. This repo has no deployment manifest for it.

Run the same checks as CI (no cluster or AWS needed; the tests use a fake store and a fake API):

```sh
pip install -r requirements-test.txt
ruff check .
ruff format --check .
python -m unittest discover -s tests
```

## RBAC

Minimum permissions the controller needs in its namespace:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: secret-sync-controller
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "create", "update"]
```

## Status

Tested with unittest and fakes only. Not run against a cluster or AWS.

## Known limitations

- Not run against a real cluster or AWS.
- Secret values are never logged.
- Only one namespace is watched.
- Rotation is handled by the poll, not by a push from the store.
