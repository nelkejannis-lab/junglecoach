import unittest
from unittest import mock
from jc import updater, friends, app

class UpdateTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(updater, "__version__", "1.15.2")
        patch.start()
        self.addCleanup(patch.stop)

    def release(self, tag="v1.16.0"):
        return {"tag_name": tag, "assets": [{"name": "Moosi-Setup.exe", "browser_download_url": f"{updater.REPOSITORY}/releases/download/{tag}/Moosi-Setup.exe"}]}

    def test_numeric_versions_and_published_windows_asset(self):
        self.assertGreater(updater.version("1.16.0"), updater.version("1.9.0"))
        self.assertTrue(updater.release_info(self.release())["available"])
        self.assertFalse(updater.release_info(self.release("v1.15.2"))["available"])
        self.assertFalse(updater.release_info(self.release("v1.14.0"))["available"])
        for mutation in ({"assets": []}, {"prerelease": True}, {"draft": True}, {"tag_name": "v2.0.0-beta"}):
            self.assertFalse(updater.release_info(dict(self.release(), **mutation))["available"])

    def test_untrusted_asset_and_network_failure(self):
        data = self.release()
        data["assets"][0]["browser_download_url"] = "https://example.org/setup.exe"
        self.assertFalse(updater.release_info(data)["available"])
        with mock.patch.object(updater, "_cached", {}), mock.patch.object(updater.urllib.request, "urlopen", side_effect=OSError):
            self.assertIn("error", updater.check())

class PresenceTests(unittest.TestCase):
    def test_cloud_online_without_league_client(self):
        result = friends.listing({"friends": ["A#EUW"]}, None, None, pub={"a#euw": {"status": {"state": "online"}}})
        self.assertEqual(result["friends"][0]["presence"], {"status": "online", "source": "moosi"})

    def test_missing_status_is_unknown(self):
        lcu = mock.Mock()
        lcu.get.return_value = [{"gameName": "A", "gameTag": "EUW"}]
        self.assertEqual(friends.presence(lcu)[0]["a#euw"]["status"], "unknown")

    def test_running_moosi_reports_online_without_league(self):
        with mock.patch.object(app, "STATUS", {"sent": None, "at": 0}), mock.patch.object(app, "cloud_bg") as send:
            app.report_status({"auth_token": "test"}, None)
            self.assertEqual(send.call_args.args[3]["state"], "online")
