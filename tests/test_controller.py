import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kubernetes.client.exceptions import ApiException

from controller import MANAGED_BY_LABEL, MANAGED_BY_VALUE, SecretStore, reconcile


def make_configmap(name="app-config", namespace="default", secret_name=None, uid="cm-uid-1"):
    annotations = {"secret-sync.example.com/source": secret_name} if secret_name else {}
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "annotations": annotations,
        }
    }


class FakeStore(SecretStore):
    def __init__(self, values):
        self._values = values

    def get(self, name):
        if name not in self._values:
            raise KeyError(name)
        return self._values[name]


class FakeApi:
    def __init__(self):
        self.secrets = {}

    def read_namespaced_secret(self, name, namespace):
        key = (name, namespace)
        if key not in self.secrets:
            raise ApiException(status=404)
        return self.secrets[key]

    def create_namespaced_secret(self, namespace, body):
        self.secrets[(body.metadata.name, namespace)] = body

    def replace_namespaced_secret(self, name, namespace, body):
        self.secrets[(name, namespace)] = body


class ReconcileTests(unittest.TestCase):
    def test_annotated_configmap_creates_secret(self):
        api = FakeApi()
        store = FakeStore({"db-password": "s3cret"})
        configmap = make_configmap(secret_name="db-password")

        reconcile(configmap, store, api)

        secret = api.secrets[("app-config-synced", "default")]
        self.assertEqual(secret.string_data, {"db-password": "s3cret"})
        self.assertEqual(secret.metadata.labels[MANAGED_BY_LABEL], MANAGED_BY_VALUE)
        owner = secret.metadata.owner_references[0]
        self.assertEqual(owner.name, "app-config")
        self.assertEqual(owner.uid, "cm-uid-1")

    def test_unannotated_configmap_is_ignored(self):
        api = FakeApi()
        store = FakeStore({})
        configmap = make_configmap(secret_name=None)

        reconcile(configmap, store, api)

        self.assertEqual(api.secrets, {})

    def test_changed_store_value_updates_existing_secret(self):
        api = FakeApi()
        store = FakeStore({"db-password": "old"})
        configmap = make_configmap(secret_name="db-password")
        reconcile(configmap, store, api)

        store = FakeStore({"db-password": "new"})
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        logging.getLogger("secret-sync").addHandler(handler)
        try:
            reconcile(configmap, store, api)
        finally:
            logging.getLogger("secret-sync").removeHandler(handler)

        secret = api.secrets[("app-config-synced", "default")]
        self.assertEqual(secret.string_data, {"db-password": "new"})
        for record in records:
            self.assertNotIn("new", record.getMessage())
            self.assertNotIn("old", record.getMessage())

    def test_secret_without_managed_by_label_is_not_overwritten(self):
        api = FakeApi()
        api.secrets[("app-config-synced", "default")] = type(
            "Secret",
            (),
            {"metadata": type("Meta", (), {"labels": {}})(), "string_data": {"db-password": "untouched"}},
        )()
        store = FakeStore({"db-password": "new"})
        configmap = make_configmap(secret_name="db-password")

        reconcile(configmap, store, api)

        secret = api.secrets[("app-config-synced", "default")]
        self.assertEqual(secret.string_data, {"db-password": "untouched"})

    def test_missing_store_secret_logs_error_and_does_not_raise(self):
        api = FakeApi()
        store = FakeStore({})
        configmap = make_configmap(secret_name="missing")

        with self.assertLogs("secret-sync", level="ERROR"):
            reconcile(configmap, store, api)

        self.assertEqual(api.secrets, {})


if __name__ == "__main__":
    unittest.main()
