import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from phase0 import PhaseError
from remote_keyboard import event_info, validate_container


class RemoteKeyboardTests(unittest.TestCase):
    def test_explicit_scrcpy_device_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'event7'
            (folder / 'device').mkdir(parents=True)
            (folder / 'device/name').write_text('scrcpy\n')
            (folder / 'dev').write_text('13:71\n')
            self.assertEqual(event_info('event7', root), dict(event='event7', major=13, minor=71))

    def test_physical_keyboard_and_path_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'event7'
            (folder / 'device').mkdir(parents=True)
            (folder / 'device/name').write_text('host-keyboard\n')
            (folder / 'dev').write_text('13:71\n')
            for event in ('event7', '../event7'):
                with self.subTest(event=event), self.assertRaises(PhaseError):
                    event_info(event, root)

    def test_only_owned_running_container_is_accepted(self):
        image = dict(State=dict(Running=True), Config=dict(Labels={'io.custom-redroid.test': 'true'}), Mounts=[])
        validate_container(image)
        image['State']['Running'] = False
        with self.assertRaises(PhaseError):
            validate_container(image)
        image['State']['Running'] = True
        image['Config']['Labels'] = {}
        with self.assertRaises(PhaseError):
            validate_container(image)

    def test_host_input_mounts_are_never_modified(self):
        image = dict(State=dict(Running=True), Config=dict(Labels={'io.custom-redroid.test': 'true'}), Mounts=[])
        for destination in ('/dev', '/dev/input', '/dev/input/event7', '/'):
            image['Mounts'] = [dict(Destination=destination)]
            with self.subTest(destination=destination), self.assertRaises(PhaseError):
                validate_container(image)


if __name__ == '__main__':
    unittest.main()
