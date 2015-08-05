from mozregression import launchers
import unittest
import os
from mock import patch, Mock
from mozprofile import Profile
from mozregression.errors import LauncherNotRunnable


class MyLauncher(launchers.Launcher):
    installed = None
    started = False
    stopped = False

    def _install(self, dest):
        self.installed = dest

    def _start(self):
        self.started = True

    def _stop(self):
        self.stopped = True


class TestLauncher(unittest.TestCase):

    def test_start_stop(self):
        launcher = MyLauncher('/foo/persist.zip')
        self.assertFalse(launcher.started)
        launcher.start()
        # now it has been started
        self.assertTrue(launcher.started)
        # restarting won't do anything because it was not stopped
        launcher.started = False
        launcher.start()
        self.assertFalse(launcher.started)
        # stop it, then start it again, this time _start is called again
        launcher.stop()
        launcher.start()
        self.assertTrue(launcher.started)


class TestMozRunnerLauncher(unittest.TestCase):

    @patch('mozregression.launchers.mozinstall')
    def setUp(self, mozinstall):
        mozinstall.get_binary.return_value = '/binary'
        self.launcher = launchers.MozRunnerLauncher('/binary')

    # patch profile_class else we will have some temporary dirs not deleted
    @patch('mozregression.launchers.MozRunnerLauncher.\
profile_class', spec=Profile)
    def launcher_start(self, profile_class, *args, **kwargs):
        self.profile_class = profile_class
        self.launcher.start(*args, **kwargs)

    def test_installed(self):
        self.assertEqual(self.launcher.binary, '/binary')

    @patch('mozregression.launchers.Runner')
    def test_start_no_args(self, Runner):
        self.launcher_start()
        kwargs = Runner.call_args[1]

        self.assertEqual(kwargs['cmdargs'], ())
        self.assertEqual(kwargs['binary'], '/binary')
        self.assertEqual(kwargs['process_args'],
                         {'processOutputLine': [self.launcher._logger.debug]})
        self.assertIsInstance(kwargs['profile'], Profile)
        # runner is started
        self.launcher.runner.start.assert_called_once_with()
        self.launcher.stop()

    @patch('mozregression.launchers.Runner')
    def test_start_with_addons(self, Runner):
        self.launcher_start(addons=['my-addon'], preferences='my-prefs')
        self.profile_class.assert_called_once_with(addons=['my-addon'],
                                                   preferences='my-prefs')
        # runner is started
        self.launcher.runner.start.assert_called_once_with()
        self.launcher.stop()

    @patch('mozregression.launchers.Runner')
    def test_start_with_profile_and_addons(self, Runner):
        self.launcher_start(profile='my-profile', addons=['my-addon'],
                            preferences='my-prefs')
        self.profile_class.clone.assert_called_once_with(
            'my-profile', addons=['my-addon'], preferences='my-prefs')
        # runner is started
        self.launcher.runner.start.assert_called_once_with()
        self.launcher.stop()

    @patch('mozregression.launchers.Runner')
    @patch('mozregression.launchers.mozversion')
    def test_get_app_infos(self, mozversion, Runner):
        mozversion.get_version.return_value = {'some': 'infos'}
        self.launcher_start()
        self.assertEqual(self.launcher.get_app_info(), {'some': 'infos'})
        mozversion.get_version.assert_called_once_with(binary='/binary')
        self.launcher.stop()

    def test_launcher_deleted_remove_tempdir(self):
        tempdir = self.launcher.tempdir
        self.assertTrue(os.path.isdir(tempdir))
        del self.launcher
        self.assertFalse(os.path.isdir(tempdir))


class TestFennecLauncher(unittest.TestCase):

    test_root = '/sdcard/tmp'

    def setUp(self):
        self.profile = Profile()
        self.addCleanup(self.profile.cleanup)
        self.remote_profile_path = self.test_root + \
            '/' + os.path.basename(self.profile.profile)

    @patch('mozregression.launchers.mozversion.get_version')
    @patch('mozregression.launchers.ADBAndroid')
    def create_launcher(self, ADBAndroid, get_version, **kwargs):
        self.adb = Mock(test_root=self.test_root)
        ADBAndroid.return_value = self.adb
        get_version.return_value = kwargs.get('version_value', {})
        return launchers.FennecLauncher('/binary')

    def test_install(self):
        self.create_launcher()
        self.adb.uninstall_app.assert_called_with("org.mozilla.fennec")
        self.adb.install_app.assert_called_with('/binary')

    @patch('mozregression.launchers.FennecLauncher._create_profile')
    def test_start_stop(self, _create_profile):
        # Force use of existing profile
        _create_profile.return_value = self.profile
        launcher = self.create_launcher()
        launcher.start(profile='my_profile')
        self.adb.exists.assert_called_once_with(self.remote_profile_path)
        self.adb.rm.assert_called_once_with(self.remote_profile_path,
                                            recursive=True)
        self.adb.push.assert_called_once_with(self.profile.profile,
                                              self.remote_profile_path)
        self.adb.launch_fennec.assert_called_once_with(
            "org.mozilla.fennec",
            extra_args=['-profile', self.remote_profile_path]
        )
        # ensure get_app_info returns something
        self.assertIsNotNone(launcher.get_app_info())
        launcher.stop()
        self.adb.stop_application.assert_called_once_with("org.mozilla.fennec")

    @patch('mozregression.launchers.FennecLauncher._create_profile')
    def test_adb_calls_with_custom_package_name(self, _create_profile):
        # Force use of existing profile
        _create_profile.return_value = self.profile
        pkg_name = 'org.mozilla.custom'
        launcher = \
            self.create_launcher(version_value={'package_name': pkg_name})
        self.adb.uninstall_app.assert_called_once_with(pkg_name)
        launcher.start(profile='my_profile')
        self.adb.launch_fennec.assert_called_once_with(
            pkg_name,
            extra_args=['-profile', self.remote_profile_path]
        )
        launcher.stop()
        self.adb.stop_application.assert_called_once_with(pkg_name)

    @patch('mozregression.launchers.ADBHost')
    @patch('__builtin__.raw_input')
    def test_check_is_runnable(self, raw_input, ADBHost):
        raw_input.return_value = 'y'
        devices = Mock(return_value=True)
        ADBHost.return_value = Mock(devices=devices)
        # this won't raise errors
        launchers.FennecLauncher.check_is_runnable()

        # exception raised if there is no device
        raw_input.return_value = 'y'
        devices.return_value = False
        self.assertRaises(LauncherNotRunnable,
                          launchers.FennecLauncher.check_is_runnable)

        # or if ADBHost().devices() raise an unexpected IOError
        devices.side_effect = OSError()
        self.assertRaises(LauncherNotRunnable,
                          launchers.FennecLauncher.check_is_runnable)
