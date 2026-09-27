import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cluster', Path(__file__).with_name('cp_join_cluster.py'))
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)


class CredentialTests(unittest.TestCase):
    def test_absent_owned_secret_delete_is_idempotent(self):
        with patch.object(c, 'certificate_secret', return_value=None), patch.object(c, 'command') as command:
            c.delete_owned_secret({'name': 'bootstrap-token-abcdef', 'uid': 'one', 'resourceVersion': '3'})
            command.assert_not_called()

    def test_replaced_secret_is_not_deleted(self):
        with patch.object(c, 'certificate_secret', return_value={'metadata': {'uid': 'other', 'resourceVersion': '3'}}), \
             patch.object(c, 'command') as command:
            with self.assertRaises(ValueError):
                c.delete_owned_secret({'name': 'bootstrap-token-abcdef', 'uid': 'one', 'resourceVersion': '3'})
            command.assert_not_called()

    def test_delete_uses_uid_and_version_preconditions(self):
        meta = {'name': 'bootstrap-token-abcdef', 'uid': 'one', 'resourceVersion': '3'}
        with patch.object(c, 'certificate_secret', return_value={'metadata': meta}), patch.object(c, 'command') as command:
            c.delete_owned_secret(meta)
            self.assertEqual(json.loads(command.call_args.args[1])['preconditions'], {'uid': 'one', 'resourceVersion': '3'})

    def test_failed_upload_revokes_token_and_clears_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            meta = {'name': 'bootstrap-token-abcdef', 'uid': 'one', 'resourceVersion': '3'}
            def command(argv, data=None, timeout=45):
                if argv[:3] == ['kubeadm', 'token', 'create']: return b'abcdef.0123456789abcdef\n'
                raise ValueError('upload failed')
            def private(path):
                path.mkdir(parents=True, mode=0o700); return path
            with patch.object(c, 'BASE', base), patch.object(c, 'private', side_effect=private), \
                 patch.object(c, 'configuration', return_value=('kind: ClusterConfiguration\n', {})), \
                 patch.object(c, 'certificate_secret', side_effect=[None, {'metadata': meta}]), \
                 patch.object(c, 'command', side_effect=command), patch.object(c, 'delete_owned_secret') as delete:
                with self.assertRaises(ValueError): c.mint('a' * 16)
                record = json.loads((base / ('a' * 16) / 'CREDENTIALS.json').read_text())
                self.assertIsNone(record['token_id'])
                self.assertEqual(record['state'], 'MINT_FAILED_CLEANUP_REQUIRED')
                self.assertNotIn('0123456789abcdef', json.dumps(record))
                delete.assert_called_once_with(meta)


if __name__ == '__main__': unittest.main()
