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
        self.log(b'old'+b'a'*(diag.MAX_LOG+100)+b'\xffEND')
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
if __name__=='__main__':unittest.main()
