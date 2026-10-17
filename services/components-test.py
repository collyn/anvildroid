#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('components',Path(__file__).with_name('runtime-components.py'))
components=importlib.util.module_from_spec(spec);spec.loader.exec_module(components)
DIGEST='a'*64
class ComponentsTests(unittest.TestCase):
    def report(self,**overrides):
        args=dict(architecture='x86_64',sdk=33,arm_report={'supported':True,'available':True})
        args.update(overrides)
        return components.catalog('r-'+'1'*32,**args)
    def row(self,report,name): return next(row for row in report['components'] if row['id']==name)
    def test_available_is_not_installed_or_cached(self):
        row=self.row(self.report(arm_report={'supported':True,'sources':[{'engine':'libndk','sha256':DIGEST,'installed':False}]}),'libndk')
        self.assertFalse(row['installed']);self.assertFalse(row['can_enable'])
        self.assertFalse(row['packages'][0]['cached'])
        self.assertEqual(row['packages'][0]['verification'],'verify_on_download')
    def test_verified_cache_is_not_runtime_installation(self):
        report=self.report(arm_report={'supported':True,'sources':[{'engine':'libhoudini','sha256':DIGEST,'version':'12','installed':True}]})
        row=self.row(report,'libhoudini')
        self.assertFalse(row['installed']);self.assertTrue(row['packages'][0]['cached'])
        self.assertEqual(components.arm_action(report,'libhoudini',True,DIGEST),{'op':'arm_set','id':'r-'+'1'*32,'enabled':True,'archive_sha256':DIGEST})
        with self.assertRaises(ValueError):components.arm_action(report,'libhoudini',True,'b'*64)
    def test_uncached_choice_rejected_when_another_package_is_cached(self):
        report=self.report(arm_report={'supported':True,'sources':[
            {'engine':'libhoudini','sha256':DIGEST,'installed':True},
            {'engine':'libhoudini','sha256':'b'*64,'installed':False}]})
        with self.assertRaises(ValueError):components.arm_action(report,'libhoudini',True,'b'*64)
    def test_conflicts_and_installed_inherited(self):
        report=self.report(arm_report={'supported':True,'enabled':True,'provider':'libhoudini-12','inherited':True,'archive_sha256':DIGEST})
        ndk=self.row(report,'libndk'); houdini=self.row(report,'libhoudini')
        self.assertEqual(ndk['conflicts'],['libhoudini'])
        self.assertFalse(ndk['installed']);self.assertTrue(houdini['installed']);self.assertTrue(houdini['inherited'])
        self.assertEqual(houdini['installed_sha256'],DIGEST)
        self.assertEqual(houdini['state_source'],'saved_configuration');self.assertIsNone(houdini['observed_active'])
        self.assertFalse(self.row(self.report(arm_report={'supported':True,'enabled':False,'provider':'libhoudini'}),'libhoudini')['installed'])
    def test_sdk_arch_and_missing_digest(self):
        for args in [dict(sdk=34),dict(architecture='aarch64')]:
            with self.subTest(args=args):
                row=self.row(self.report(**args),'libndk');self.assertFalse(row['can_enable']);self.assertEqual(row['status'],'incompatible')
        report=self.report(arm_report={'supported':True,'sources':[{'engine':'libhoudini'},{'engine':'libhoudini','sha256':'bad'}]})
        self.assertEqual(self.row(report,'libhoudini')['packages'],[])
    def test_malformed_untrusted_source_data(self):
        for value in [None,False,42,'oops',{}, {'sources':{}}, {'sources':42}, {'sources':'bad'}, {'sources':[None,False,{},'bad']}]:
            with self.subTest(value=value):
                report=self.report(arm_report=value)
                self.assertEqual(len(report['components']),6)
    def test_google_unknown_partial_complete_and_configured(self):
        self.assertIsNone(self.row(self.report(selection={'flavor':'GAPPS'}),'gapps')['installed'])
        self.assertFalse(self.row(self.report(google_report={'packages':{'com.android.vending':True}}),'gapps')['installed'])
        packages={key:True for key in ['com.android.vending','com.google.android.gms','com.google.android.gsf']}
        self.assertTrue(self.row(self.report(google_report={'packages':packages}),'gapps')['installed'])
        for value in [None,[],42,'GAPPS']:
            with self.subTest(value=value):
                self.assertIsNone(self.row(self.report(google_report=value,selection=value),'gapps')['installed'])
    def test_extensions_truthful_and_no_scripts(self):
        report=self.report()
        for name in ['microg','magisk','smartdock']:
            row=self.row(report,name);self.assertIsNone(row['installed']);self.assertFalse(row['can_enable']);self.assertEqual(row['status'],'unavailable')
            with self.assertRaises(ValueError):components.arm_action(report,name,True)
    def test_runtime_validation(self):
        for name in ['../other','r-invalid','',None]:
            with self.assertRaises(ValueError):components.catalog(name,architecture='x86_64',sdk=33,arm_report={})
    def test_default_requires_saved_provider_before_restore(self):
        for saved in [False, True]:
            report=components.catalog('default',architecture='x86_64',sdk=33,
                arm_report={'supported':True,'available':True,'restore_available':saved})
            row=self.row(report,'libndk')
            self.assertEqual(row['can_enable'],saved)
            self.assertEqual(row['packages'][0]['verification'],'existing_libraries')
            self.assertEqual(row['packages'][0]['version'],'Unknown (existing provider)')
            if saved:
                self.assertEqual(components.arm_action(report,'libndk',True),{'op':'arm_set','id':'default','enabled':True})
            else:
                with self.assertRaises(ValueError):components.arm_action(report,'libndk',True)
    def test_default_arm_info_reports_real_saved_restore_configuration(self):
        spec = importlib.util.spec_from_file_location('default_arm_fixture', Path(__file__).with_name('runtime-existing.py'))
        existing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(existing)
        with tempfile.TemporaryDirectory(prefix='anvil-default-arm-') as folder:
            root = Path(folder)
            work = root / 'work'
            work.mkdir()
            for library in ('lib', 'lib64'):
                directory = work / 'overlay/system' / library
                directory.mkdir(parents=True)
                (directory / 'libndk_translation.so').touch()
            runtime = existing.ExistingRuntime(root, work)
            # This fixture tests reporting, independently of the host ancestor
            # ownership policy enforced by the adapter's read boundary.
            runtime.read = lambda path: path.read_text()
            for provider in ('libndk_translation.so', '0', 'unknown.so'):
                (work / 'waydroid.prop').write_text('ro.dalvik.vm.native.bridge=' + provider + '\n')
                runtime.metadata.unlink(missing_ok=True)
                report = runtime.arm_info()
                self.assertFalse(report['restore_available'])
                self.assertEqual(report['enabled'], provider != '0')
                self.assertEqual(report['supported'], provider != 'unknown.so')
                for saved in ({}, {'ro.dalvik.vm.native.bridge':'libndk_translation.so'}):
                    runtime.metadata.write_text(json.dumps({'owner_uid':1000, 'arm_properties':saved}))
                    self.assertEqual(runtime.arm_info()['restore_available'], bool(saved))
if __name__=='__main__':unittest.main()
