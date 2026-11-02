"""Syncs secrets from a secret store into Kubernetes Secrets, driven by ConfigMap annotations."""

import logging
import os
import time

from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException

ANNOTATION = "secret-sync.example.com/source"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "secret-sync-controller"
RESYNC_SECONDS = 60

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("secret-sync")


class SecretStore:
    """Reads a named secret value from a backing store."""

    def get(self, name: str) -> str:
        raise NotImplementedError


class AwsSecretStore(SecretStore):
    """Reads secret values from AWS Secrets Manager."""

    def __init__(self, region_name=None):
        import boto3  # kept local so tests never need boto3 or AWS access

        self._client = boto3.client("secretsmanager", region_name=region_name)

    def get(self, name: str) -> str:
        response = self._client.get_secret_value(SecretId=name)
        return response["SecretString"]


def _target_secret_name(configmap_name: str) -> str:
    return f"{configmap_name}-synced"


def _build_secret_body(configmap, target_name, namespace, secret_key, secret_value):
    metadata = configmap["metadata"]
    owner_ref = client.V1OwnerReference(
        api_version="v1",
        kind="ConfigMap",
        name=metadata["name"],
        uid=metadata["uid"],
        controller=True,
        block_owner_deletion=True,
    )
    return client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=target_name,
            namespace=namespace,
            labels={MANAGED_BY_LABEL: MANAGED_BY_VALUE},
            owner_references=[owner_ref],
        ),
        string_data={secret_key: secret_value},
    )


def reconcile(configmap, store: SecretStore, api):
    """Creates or updates the Secret for one ConfigMap. Never raises."""
    metadata = configmap["metadata"]
    secret_key = metadata.get("annotations", {}).get(ANNOTATION) if metadata.get("annotations") else None
    if not secret_key:
        return

    name = metadata["name"]
    namespace = metadata["namespace"]
    target_name = _target_secret_name(name)

    try:
        secret_value = store.get(secret_key)
    except Exception:  # noqa: BLE001 - any store failure should be logged, not crash the loop
        log.error("could not read secret %r for configmap %r", secret_key, name)
        return

    try:
        existing = api.read_namespaced_secret(target_name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        existing = None

    body = _build_secret_body(configmap, target_name, namespace, secret_key, secret_value)

    if existing is None:
        api.create_namespaced_secret(namespace, body)
        return

    labels = existing.metadata.labels or {}
    if labels.get(MANAGED_BY_LABEL) != MANAGED_BY_VALUE:
        log.warning("secret %r is not managed by this controller, skipping", target_name)
        return

    api.replace_namespaced_secret(target_name, namespace, body)


def _to_dict(configmap_event_object):
    return configmap_event_object.to_dict()


def main():
    namespace = os.environ["NAMESPACE"]
    config.load_incluster_config()

    api = client.CoreV1Api()
    store = AwsSecretStore()

    def resync():
        for configmap in api.list_namespaced_config_map(namespace).items:
            reconcile(_to_dict(configmap), store, api)

    resync()
    last_resync = time.time()

    watcher = watch.Watch()
    for event in watcher.stream(
        api.list_namespaced_config_map, namespace=namespace, timeout_seconds=RESYNC_SECONDS
    ):
        reconcile(_to_dict(event["object"]), store, api)
        if time.time() - last_resync >= RESYNC_SECONDS:
            resync()
            last_resync = time.time()


if __name__ == "__main__":
    main()
