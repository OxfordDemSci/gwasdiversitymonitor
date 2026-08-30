import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import generate_data


class RecordingSMTP:
    connections = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_count = 0
        self.messages = []
        type(self).connections.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def ehlo(self):
        self.ehlo_count += 1

    def send_message(self, message):
        self.messages.append(message)


class FailureNotificationTests(unittest.TestCase):
    def setUp(self):
        RecordingSMTP.connections = []
        self.environment = {
            'GWAS_DEPLOYMENT_DOMAIN': 'gwasdiversitymonitor.com',
            'GWAS_FAILURE_EMAIL_FROM': (
                'alerts@mail.gwasdiversitymonitor.com'
            ),
            'GWAS_FAILURE_EMAIL_TO': (
                'contact@gwasdiversitymonitor.com, owner@example.test'
            ),
            'GWAS_FAILURE_EMAIL_COOLDOWN_SECONDS': '21600',
        }
        self.now = datetime.datetime(
            2026, 8, 30, 22, 15, tzinfo=datetime.timezone.utc
        )

    def _send(self, directory, error=None, now=None, environment=None):
        error = error or RuntimeError('catalog download failed')
        with mock.patch.object(
                generate_data.smtplib, 'SMTP', RecordingSMTP), \
                mock.patch.object(
                    generate_data.socket,
                    'gethostname',
                    return_value='gwas-data-01',
                ):
            return generate_data.send_failure_notification(
                error,
                'Traceback\nRuntimeError: catalog download failed',
                directory,
                exit_status=1,
                environ=(
                    self.environment if environment is None else environment
                ),
                now=now or self.now,
            )

    def test_failure_email_contains_diagnostics_and_uses_local_mta(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._send(directory)
            state_path = generate_data._failure_notification_state_path(
                directory
            )
            with open(state_path, encoding='utf-8') as state_file:
                state = json.load(state_file)

        self.assertEqual(outcome, 'sent')
        self.assertEqual(len(RecordingSMTP.connections), 1)
        connection = RecordingSMTP.connections[0]
        self.assertEqual(
            (connection.host, connection.port, connection.timeout),
            ('127.0.0.1', 25, 15.0),
        )
        self.assertEqual(connection.ehlo_count, 1)
        message = connection.messages[0]
        self.assertEqual(
            message['From'], 'alerts@mail.gwasdiversitymonitor.com'
        )
        self.assertEqual(
            message['To'],
            'contact@gwasdiversitymonitor.com, owner@example.test',
        )
        self.assertIn('generate_data.py failed', message['Subject'])
        self.assertIn('gwas-data-01', message.get_content())
        self.assertIn('Exit status: 1', message.get_content())
        self.assertIn('catalog download failed', message.get_content())
        self.assertIn('signature', state)

    def test_identical_restart_failure_is_suppressed_but_new_error_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self._send(directory), 'sent')
            one_hour_later = self.now + datetime.timedelta(hours=1)
            self.assertEqual(
                self._send(directory, now=one_hour_later), 'suppressed'
            )
            self.assertEqual(
                self._send(
                    directory,
                    error=ValueError('invalid release manifest'),
                    now=one_hour_later,
                ),
                'sent',
            )

        self.assertEqual(len(RecordingSMTP.connections), 2)

    def test_non_loopback_mail_relay_is_rejected(self):
        environment = dict(self.environment)
        environment['GWAS_LOCAL_MAIL_HOST'] = 'smtp.example.test'
        with tempfile.TemporaryDirectory() as directory, \
                self.assertRaisesRegex(ValueError, 'loopback'):
            self._send(directory, environment=environment)

        self.assertEqual(RecordingSMTP.connections, [])

    def test_non_production_copies_never_connect_to_smtp(self):
        for deployment_domain in (
                '', 'localhost', 'staging.gwasdiversitymonitor.com',
                'www.gwasdiversitymonitor.com',
                'gwasdiversitymonitor.com.example.test'):
            with self.subTest(deployment_domain=deployment_domain), \
                    tempfile.TemporaryDirectory() as directory:
                environment = dict(self.environment)
                environment['GWAS_DEPLOYMENT_DOMAIN'] = deployment_domain
                outcome = self._send(
                    directory, environment=environment
                )

                self.assertEqual(outcome, 'not-production')
                self.assertFalse(os.path.exists(
                    generate_data._failure_notification_state_path(directory)
                ))

        self.assertEqual(RecordingSMTP.connections, [])

    def test_main_notifies_and_preserves_failure_exit_status(self):
        logger = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    generate_data.os, 'getcwd', return_value=directory
                ), \
                mock.patch.object(
                    generate_data, 'setup_logging', return_value=logger
                ), \
                mock.patch.object(
                    generate_data,
                    'generate_and_publish',
                    side_effect=RuntimeError('generation failed'),
                ), \
                mock.patch.object(
                    generate_data, '_notify_generation_failure'
                ) as notify, \
                mock.patch.object(generate_data.logging, 'shutdown'):
            status = generate_data.main()

        self.assertEqual(status, 1)
        notify.assert_called_once()
        self.assertIsInstance(notify.call_args.args[0], RuntimeError)
        self.assertEqual(notify.call_args.args[1:], (directory, 1, logger))

    def test_success_clears_the_previous_failure_cooldown(self):
        logger = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    generate_data.os, 'getcwd', return_value=directory
                ), \
                mock.patch.object(
                    generate_data, 'setup_logging', return_value=logger
                ), \
                mock.patch.object(
                    generate_data,
                    'generate_and_publish',
                    return_value='unchanged',
                ), \
                mock.patch.object(
                    generate_data, '_clear_failure_notification_state'
                ) as clear_state, \
                mock.patch.object(generate_data.logging, 'shutdown'):
            status = generate_data.main()

        self.assertEqual(status, 0)
        clear_state.assert_called_once_with(directory, logger)


if __name__ == '__main__':
    unittest.main()
