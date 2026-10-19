#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('diagnostics',Path(__file__).with_name('runtime-diagnostics.py'))
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)

class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.real_fstat=os.fstat
    def tearDown(self):self.temp.cleanup()
    def root_stat(self,fd):
        info=self.real_fstat(fd)
        return types.SimpleNamespace(st_uid=0,st_mode=info.st_mode,st_nlink=info.st_nlink,st_size=info.st_size,st_mtime_ns=info.st_mtime_ns)
    def log(self,data=b'hello'):
        path=self.root/'worker.log';path.write_bytes(data);path.chmod(0o600);return path
    def tail(self,name='worker.log'):
        with patch.object(diag.os,'fstat',side_effect=self.root_stat):return diag.log_tail(self.root,name)
    def test_bounded_invalid_encoding_and_tail(self):
        self.log(b'old'+b'a'*(diag.MAX_LOG+100)+b'\n\xffEND')
        result=self.tail();self.assertTrue(result['truncated']);self.assertLessEqual(len(result['text']),diag.MAX_LOG)
        self.assertTrue(result['text'].endswith('\ufffdEND'));self.assertFalse(result['changed_during_read'])
    def test_symlink_fifo_hardlink_and_writeable_files_rejected(self):
        path=self.log()
        for kind in ['symlink','fifo','hardlink','writeable']:
            with self.subTest(kind=kind):
                path.unlink(missing_ok=True)
                other=self.root/'other';other.unlink(missing_ok=True)
                if kind=='symlink':other.write_text('secret');path.symlink_to(other)
                elif kind=='fifo':os.mkfifo(path)
                elif kind=='hardlink':other.write_text('secret');os.link(other,path)
                else:path.write_text('bad');path.chmod(0o666)
                with self.assertRaises((ValueError,OSError)):self.tail()
    def test_directory_privacy_and_symlink(self):
        self.log();self.root.chmod(0o755)
        with self.assertRaises(ValueError):self.tail()
        self.root.chmod(0o700)
        link=self.root/'link';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):diag.log_tail(link,'worker.log')
        with patch.object(diag.os,'fstat',return_value=types.SimpleNamespace(st_uid=1000,st_mode=0o40700)):
            with self.assertRaises(ValueError):diag.log_tail(self.root,'worker.log')
    def test_changes_during_read_are_reported(self):
        path=self.log();real_pread=os.pread
        def changed(fd,count,offset):
            data=real_pread(fd,count,offset)
            with path.open('ab') as stream:stream.write(b'changed')
            return data
        with patch.object(diag.os,'pread',side_effect=changed):self.assertTrue(self.tail()['changed_during_read'])
    def test_no_implicit_logs_and_partial_failures(self):
        def broken():raise RuntimeError('token=PRIVATE /home/alice/private 10.0.0.1')
        report=diag.collect('default',{'runtime':lambda:{'ok':True},'health':broken})
        self.assertEqual(report['logs'],{});self.assertEqual(report['sections']['runtime']['status'],'collected')
        self.assertEqual(report['sections']['health']['status'],'unavailable')
        self.assertNotIn('PRIVATE',json.dumps(report));self.assertNotIn('alice',json.dumps(report))
        report=diag.collect('default',{},include_logs=True)
        self.assertTrue(all(x['status']=='unavailable' for x in report['logs'].values()))
    def test_credentials_addresses_paths_and_structured_keys(self):
        source='password=ONE Bearer TWO https://alice:THREE@example.org/?token=FOUR /home/alice/private 192.168.1.2:99 2001:db8::1 alice@example.org'
        result=diag.redact_text(source)
        for secret in ['ONE','TWO','THREE','FOUR','/home/alice','192.168.1.2','2001:db8::1','alice@example.org']:self.assertNotIn(secret,result)
        result=diag.redact({'api_key':'FIVE','password':'SIX','nested':{'token':'SEVEN'}})
        for secret in ['FIVE','SIX','SEVEN']:self.assertNotIn(secret,json.dumps(result))
    def test_environment_style_credentials(self):
        result=diag.redact_text('AWS_SECRET_ACCESS_KEY=ENVSECRET access_token=ACCESSTOKEN client_secret=CLIENTSECRET')
        for secret in ['ENVSECRET','ACCESSTOKEN','CLIENTSECRET']:self.assertNotIn(secret,result)
    def test_json_log_credentials(self):
        result=diag.redact_text('{"password":"HUNTER2", "api_key": "SECRET3", "authorization": "Bearer TOKEN4"}')
        for secret in ['HUNTER2','SECRET3','TOKEN4']:self.assertNotIn(secret,result)
    def test_escaped_quotes_and_authentication_headers(self):
        source = r'''{"password":"prefix\"QUOTED_SECRET", "token":'prefix\'SINGLE_SECRET'}'''
        source += '\nAuthorization: Digest username="AUTH_USER", nonce="AUTH_NONCE", response="AUTH_RESPONSE"'
        result = diag.redact_text(source)
        for secret in ('QUOTED_SECRET', 'SINGLE_SECRET', 'AUTH_USER', 'AUTH_NONCE', 'AUTH_RESPONSE'):
            self.assertNotIn(secret, result)
    def test_unterminated_quoted_credentials_consume_the_partial_line(self):
        for source in ('password="first PRIVATE_SUFFIX WITH SPACES',
                       "token='first PRIVATE_SUFFIX WITH SPACES\nstate=Running",
                       'password="first PRIVATE_SUFFIX WITH SPACES\\'):
            with self.subTest(source=source):
                result = diag.redact_text(source)
                self.assertNotIn('PRIVATE_SUFFIX', result)
                self.assertNotIn('WITH SPACES', result)
                if '\nstate=Running' in source: self.assertIn('state=Running', result)
        self.assertIn('state=Running', diag.redact_text('password="secret" state=Running'))
    def test_key_sensitivity_is_checked_before_identifying_path_is_masked(self):
        for key in ('/home/password/file', r'/home/passw\u006frd/file',
                    '/home/pass\u200bword/file', 'passw\\u006frd'):
            with self.subTest(key=key):
                result = diag.redact({key: 'PRIVATE_VALUE'})
                self.assertNotIn('PRIVATE_VALUE', json.dumps(result))
                self.assertIn('<redacted>', result.values())
    def test_private_key_blocks_and_truncated_pem_prefix(self):
        for source in ('before\n-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE_BODY\n-----END OPENSSH PRIVATE KEY-----\nafter',
                       '-----BEGIN PRIVATE KEY-----\nUNFINISHED_BODY',
                       'TAIL_BODY\n-----END RSA PRIVATE KEY-----\nafter'):
            with self.subTest(source=source):
                result = diag.redact_text(source)
                for secret in ('PRIVATE_BODY', 'UNFINISHED_BODY', 'TAIL_BODY'):
                    self.assertNotIn(secret, result)
        self.assertIn('after', diag.redact_text(source))
    def test_identifying_fields_paths_and_dictionary_keys(self):
        source = ('hostname=PRIVATE_HOST SSID="PRIVATE_WIFI" gsf_id=PRIVATE_GSF '
                  '/run/user/1001/private.sock /mnt/PRIVATE_DRIVE/project '
                  '/media/PRIVATE_USER/disk C:\\Users\\PRIVATE_WINDOWS\\file '
                  'ssh://PRIVATE_LOGIN:PRIVATE_PASSWORD@remote.example/path')
        result = diag.redact_text(source)
        for secret in ('PRIVATE_', '/run/user/1001', '/mnt/', '/media/', 'C:\\Users\\'):
            self.assertNotIn(secret, result)
        result = diag.redact({'/home/PRIVATE_PERSON/file': 'kept',
                              'PRIVATE_EMAIL@example.org': 'kept', '10.21.32.43': 'kept'})
        encoded = json.dumps(result)
        for secret in ('PRIVATE_', '10.21.32.43'):
            self.assertNotIn(secret, encoded)
        self.assertIn('kept', encoded)
    def test_common_serialized_and_terminal_encodings(self):
        sources = [r'passw\u006frd\u003dENCODED_SECRET',
                   r'peer=10\u002e21\u002e32\u002e43',
                   r'passw\\u006frd\\u003dDOUBLE_SECRET',
                   r'passw\x6frd=HEX_SECRET',
                   'pass\x1b[31mword=ANSI_SECRET',
                   'pass\u200bword=INVISIBLE_SECRET',
                   '\x00'.join('password=UTF16_SECRET')]
        for source in sources:
            with self.subTest(source=source):
                result = diag.redact_text(source)
                for secret in ('ENCODED_SECRET', 'DOUBLE_SECRET', 'HEX_SECRET', 'ANSI_SECRET',
                               'INVISIBLE_SECRET', 'UTF16_SECRET', '10.21.32.43'):
                    self.assertNotIn(secret, result)
                self.assertIn('<ip-address>' if source.startswith('peer=') else '<redacted>', result)
    def test_serialized_surrogates_remain_literal_and_utf8_safe(self):
        for source in (r'partial=\uD800', r'partial=\uDFFF', r'pair=\uD83D\uDE00',
                       r'nested=\\uD800', r'scalar=\U0001F600'):
            with self.subTest(source=source):
                report = diag.collect('default', {'health': lambda: {'detail': source}})
                text = report['sections']['health']['data']['detail']
                self.assertFalse(any(0xd800 <= ord(char) <= 0xdfff for char in text))
                encoded = json.dumps(report, ensure_ascii=False).encode('utf-8')
                self.assertEqual(json.loads(encoded), report)
                # Default ASCII JSON also retains backslashes as literal text,
                # rather than emitting the lone JSON surrogate serde rejects.
                if source.startswith('scalar='):
                    self.assertEqual(text, 'scalar=\U0001f600')
                else:
                    self.assertNotRegex(json.dumps(report), r'(?<!\\)(?:\\\\)*\\u[dD][89a-fA-F][0-9a-fA-F]{2}')
    def test_truncated_tail_discards_partial_credential_line(self):
        self.log(b'password=' + b'SENSITIVE' * (diag.MAX_LOG // 8 + 10) + b'\nready=true\n')
        result = self.tail()
        self.assertTrue(result['truncated'])
        self.assertFalse('SENSITIVE' in result['text'], 'Partial credential line survived tailing')
        self.assertIn('ready=true', result['text'])
    def test_runtime_like_report_keeps_operational_fields(self):
        report = diag.collect('r-'+'1'*32, {'runtime': lambda: {
            'state': 'Running', 'generation': 'a'*32, 'cleanup_complete': False,
            'display_backend': 'native-x11', 'network': 'isolated',
            'error': r'Binder device /dev/anbox-binder failed; password="safe\"PRIVATE_SUFFIX"',
            'nested': {'owner_uid': 1001, 'ssid': 'PRIVATE_WIFI', 'gpu_node': '/dev/dri/renderD128'},
        }})
        data = report['sections']['runtime']['data']
        self.assertEqual(data['state'], 'Running')
        self.assertEqual(data['generation'], 'a'*32)
        self.assertEqual(data['display_backend'], 'native-x11')
        self.assertEqual(data['network'], 'isolated')
        self.assertIs(data['cleanup_complete'], False)
        self.assertEqual(data['nested']['gpu_node'], '/dev/dri/renderD128')
        self.assertIn('/dev/anbox-binder', data['error'])
        self.assertNotIn('PRIVATE_', json.dumps(report))
        self.assertEqual(report['privacy']['redaction'], 'best_effort')
    def test_limits_and_validation(self):
        report=diag.collect('default',{'runtime':lambda:{'value':'a'*(256*1024)}})
        self.assertEqual(report['sections']['runtime']['status'],'unavailable')
        with patch.object(diag,'MAX_REPORT',32):
            with self.assertRaisesRegex(ValueError,'report exceeds'):diag.collect('default',{})
        for runtime in ['../other','r-invalid',None]:
            with self.assertRaises(ValueError):diag.collect(runtime,{})
        with self.assertRaises(ValueError):diag.collect('default',{},include_logs='yes')
        with self.assertRaises(ValueError):diag.collect('default',{'private':lambda:'bad'})
        with self.assertRaises(ValueError):diag.log_tail(self.root,'../worker.log')
        nested={};current=nested
        for _ in range(20):current['child']={};current=current['child']
        self.assertIn('depth-limit',json.dumps(diag.redact(nested)))
    def test_stable_verified_session_keeps_partial_failures_and_logs_opt_in(self):
        verify = unittest.mock.Mock()
        def broken(): raise RuntimeError('Health unavailable')
        with patch.object(diag, 'log_tail') as tail:
            report = diag.collect('default', {'runtime':lambda:{'state':'Stopped'}, 'health':broken},
                                  verify_session=verify)
        self.assertEqual(report['session']['status'], 'verified')
        self.assertEqual(report['sections']['runtime']['status'], 'collected')
        self.assertEqual(report['sections']['health']['status'], 'unavailable')
        self.assertEqual(report['logs'], {})
        self.assertGreater(verify.call_count, 2)
        tail.assert_not_called()

    def test_changed_section_discards_earlier_data_and_stops_remaining_collectors(self):
        for failure in (False, True):
            current = ['first']
            later = unittest.mock.Mock(return_value={'later':'MUST_NOT_COLLECT'})
            def verify():
                if current[0] != 'first': raise RuntimeError('Runtime changed during diagnostic collection')
            def changes():
                current[0] = 'second'
                if failure: raise RuntimeError('Section failed after restart')
                return {'mixed':'MUST_NOT_EXPORT'}
            with self.subTest(failure=failure), patch.object(diag, 'log_tail') as tail:
                report = diag.collect('default', {'runtime':lambda:{'old':'MUST_NOT_EXPORT'},
                                      'health':changes, 'resources':later}, include_logs=True,
                                      verify_session=verify)
                self.assertEqual(report['session']['status'], 'unavailable')
                self.assertIn('Runtime changed', report['session']['error'])
                self.assertTrue(all(row['status']=='unavailable' and 'data' not in row
                                    for row in report['sections'].values()))
                self.assertTrue(all(row['status']=='unavailable' and 'text' not in row
                                    for row in report['logs'].values()))
                self.assertNotIn('MUST_NOT_EXPORT', json.dumps(report))
                later.assert_not_called(); tail.assert_not_called()

    def test_missing_session_skips_all_reads_and_redacts_verification_errors(self):
        def unavailable(): raise RuntimeError('Lost session token=PRIVATE /home/alice/private')
        callback = unittest.mock.Mock()
        with patch.object(diag, 'log_tail') as tail:
            report = diag.collect('default', {'health':callback}, include_logs=True,
                                  verify_session=unavailable)
        callback.assert_not_called(); tail.assert_not_called()
        self.assertEqual(report['session']['status'], 'unavailable')
        self.assertNotIn('PRIVATE', json.dumps(report)); self.assertNotIn('alice', json.dumps(report))
        self.assertIn('Retry diagnostic collection', report['sections']['health']['error'])

    def test_restart_during_logs_discards_sections_and_first_log_and_does_not_read_new_generation(self):
        generation = ['a'*32]
        def verify():
            if generation[0] != 'a'*32: raise RuntimeError('Runtime changed during diagnostic collection')
        def tail(directory, name):
            generation[0] = 'b'*32
            return {'status':'collected', 'text':'OLD_SESSION_LOG'}
        with patch.object(diag, 'log_tail', side_effect=tail) as read:
            report = diag.collect('r-'+'1'*32, {'health':lambda:{'gpu':'OLD_SESSION_DATA'}},
                                  include_logs=True, log_directory=self.root, generation='a'*32,
                                  verify_session=verify)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(report['session']['status'], 'unavailable')
        self.assertNotIn('OLD_SESSION', json.dumps(report))
        self.assertIn('x11-'+'a'*32+'.log', report['logs'])
        self.assertNotIn('x11-'+'b'*32+'.log', report['logs'])
        self.assertTrue(all(row['status']=='unavailable' for row in report['logs'].values()))

    def test_process_start_parses_parentheses_and_rejects_missing_identity(self):
        fields = ['S'] + ['0']*18 + ['123456'] + ['0']*4
        with patch.object(diag.Path, 'read_text', return_value='91 (session (with) spaces) '+' '.join(fields)):
            self.assertEqual(diag.process_start('91'), '123456')
        for pid in (None, 0, '0', '-1', '91/../../other', 'not-a-pid'):
            with self.subTest(pid=pid), self.assertRaises(ValueError): diag.process_start(pid)

if __name__=='__main__':unittest.main()
