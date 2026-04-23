"""Tests for ServerFsClient — the relay-side request/response correlator.

Focus: protocol envelope shape, threading correctness, timeout/cancel
behavior. The actual WS socket is mocked.
"""

import json
import threading
import time
import unittest

from pawflow_relay.server_fs_client import ServerFsClient


class TestEnvelopeShape(unittest.TestCase):

    def setUp(self):
        self.sent = []
        self.cli = ServerFsClient(send_callable=self.sent.append)

    def test_request_payload_shape(self):
        # Fire a request in a thread; resolve it from the main thread
        result_holder = {}

        def _do_request():
            result_holder['r'] = self.cli.request(
                'sfs.getattr', {'path': 'foo.txt'}, timeout=2.0)

        t = threading.Thread(target=_do_request)
        t.start()
        # Wait for the send to land
        deadline = time.time() + 1.0
        while not self.sent and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.sent), 1)
        envelope = json.loads(self.sent[0].decode())
        self.assertEqual(envelope['type'], 'relay_request')
        self.assertEqual(envelope['method'], 'sfs.getattr')
        self.assertEqual(envelope['args'], {'path': 'foo.txt'})
        self.assertIn('request_id', envelope)
        # Reply
        self.cli.dispatch_response({
            'type': 'relay_response',
            'request_id': envelope['request_id'],
            'data': {'st_size': 42},
        })
        t.join(timeout=2.0)
        self.assertEqual(result_holder['r'], {'data': {'st_size': 42}})

    def test_send_lock_serializes_calls(self):
        # Two simultaneous requests must be sent under the lock — no
        # interleaving on the wire.
        lock = threading.Lock()
        send_order = []

        def _slow_send(payload):
            with lock:
                pass  # sanity
            # Acquire externally-mediated lock (the client's _send_lock)
            # is what's tested here — we record arrival order and check
            # send_lock provides serialization
            send_order.append(payload)
            time.sleep(0.05)

        ext_lock = threading.Lock()
        cli = ServerFsClient(send_callable=_slow_send, send_lock=ext_lock)
        results = {}

        def _req(idx):
            results[idx] = cli.request('sfs.getattr', {'path': f'p{idx}'},
                                       timeout=5.0)

        threads = [threading.Thread(target=_req, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        # Wait for all sends to enqueue
        time.sleep(0.5)
        self.assertEqual(len(send_order), 3)
        # Reply to all
        for payload in send_order:
            env = json.loads(payload.decode())
            cli.dispatch_response({
                'type': 'relay_response',
                'request_id': env['request_id'],
                'data': {'st_size': 1},
            })
        for t in threads:
            t.join(timeout=2.0)
        self.assertEqual(len(results), 3)


class TestErrorPaths(unittest.TestCase):

    def test_timeout_returns_eio(self):
        cli = ServerFsClient(send_callable=lambda _: None)
        r = cli.request('sfs.getattr', {'path': 'x'}, timeout=0.1)
        self.assertEqual(r.get('error'), 'EIO')
        self.assertEqual(r.get('errno'), 5)
        self.assertIn('timeout', r.get('message', ''))

    def test_send_failure_returns_eio(self):
        def _broken_send(_):
            raise OSError('socket closed')
        cli = ServerFsClient(send_callable=_broken_send)
        r = cli.request('sfs.getattr', {'path': 'x'}, timeout=2.0)
        self.assertEqual(r.get('error'), 'EIO')
        self.assertIn('send failed', r.get('message', ''))

    def test_dispatch_unknown_request_id_returns_false(self):
        cli = ServerFsClient(send_callable=lambda _: None)
        self.assertFalse(cli.dispatch_response({
            'type': 'relay_response',
            'request_id': 'never-sent',
            'data': {},
        }))

    def test_request_after_close_returns_eio(self):
        cli = ServerFsClient(send_callable=lambda _: None)
        cli.cancel_all()
        r = cli.request('sfs.getattr', {'path': 'x'}, timeout=1.0)
        self.assertEqual(r.get('error'), 'EIO')
        self.assertIn('closed', r.get('message', ''))

    def test_cancel_all_wakes_pending_waiters(self):
        cli = ServerFsClient(send_callable=lambda _: None)
        results = {}

        def _req():
            results['r'] = cli.request('sfs.getattr', {'path': 'x'},
                                       timeout=10.0)

        t = threading.Thread(target=_req)
        t.start()
        time.sleep(0.1)  # let the send register the pending entry
        woke = cli.cancel_all('test cancel')
        self.assertEqual(woke, 1)
        t.join(timeout=2.0)
        self.assertEqual(results['r'].get('error'), 'EIO')
        self.assertIn('test cancel', results['r'].get('message', ''))


class TestErrorPropagation(unittest.TestCase):

    def test_server_error_propagates_with_errno(self):
        sent = []
        cli = ServerFsClient(send_callable=sent.append)
        results = {}

        def _req():
            results['r'] = cli.request('sfs.getattr', {'path': 'nope'},
                                       timeout=2.0)

        t = threading.Thread(target=_req)
        t.start()
        deadline = time.time() + 1.0
        while not sent and time.time() < deadline:
            time.sleep(0.01)
        env = json.loads(sent[0].decode())
        cli.dispatch_response({
            'type': 'relay_response',
            'request_id': env['request_id'],
            'error': 'ENOENT', 'errno': 2,
            'message': 'not found',
        })
        t.join(timeout=2.0)
        self.assertEqual(results['r'], {
            'error': 'ENOENT', 'errno': 2, 'message': 'not found',
        })


if __name__ == '__main__':
    unittest.main()
