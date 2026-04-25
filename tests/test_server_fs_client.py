"""Tests for ServerFsClient — the relay-side request/response correlator.

Focus: protocol envelope shape, threading correctness, timeout/cancel
behavior. The actual WS socket is mocked.
"""

import json
import threading
import time
import unittest

from pawflow_relay.server_fs_client import (
    ServerFsClient,
    SwappableServerFsClient,
)


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


class TestSwappableServerFsClient(unittest.TestCase):
    """The swappable handle is what the FUSE mount holds for its lifetime.

    Without it, every WS reconnect would unmount + remount the FUSE,
    invalidating kernel inodes and breaking bind-mounts in downstream
    containers (e.g. CC docker bind of /cc_sessions).
    """

    def test_request_with_no_inner_returns_eio(self):
        swap = SwappableServerFsClient()
        r = swap.request('sfs.getattr', {'path': 'foo'}, timeout=0.5)
        self.assertEqual(r['error'], 'EIO')
        self.assertEqual(r['errno'], 5)
        self.assertIn('reconnecting', r['message'])

    def test_set_inner_then_request_forwards(self):
        sent = []
        cli = ServerFsClient(send_callable=sent.append)
        swap = SwappableServerFsClient()
        swap.set_inner(cli)

        results = {}

        def _req():
            results['r'] = swap.request(
                'sfs.getattr', {'path': 'foo'}, timeout=2.0)

        t = threading.Thread(target=_req)
        t.start()
        deadline = time.time() + 1.0
        while not sent and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(sent), 1)
        env = json.loads(sent[0].decode())
        cli.dispatch_response({
            'type': 'relay_response',
            'request_id': env['request_id'],
            'data': {'st_size': 7},
        })
        t.join(timeout=2.0)
        self.assertEqual(results['r'], {'data': {'st_size': 7}})

    def test_clear_inner_then_request_returns_eio(self):
        cli = ServerFsClient(send_callable=lambda b: None)
        swap = SwappableServerFsClient()
        swap.set_inner(cli)
        swap.clear_inner()
        r = swap.request('sfs.getattr', {'path': 'foo'}, timeout=0.5)
        self.assertEqual(r['error'], 'EIO')

    def test_swap_old_to_new_routes_subsequent_requests_to_new(self):
        sent_old = []
        sent_new = []
        cli_old = ServerFsClient(send_callable=sent_old.append)
        cli_new = ServerFsClient(send_callable=sent_new.append)
        swap = SwappableServerFsClient()
        swap.set_inner(cli_old)

        # First request goes through old
        def _req(out):
            out['r'] = swap.request('sfs.getattr', {'p': 1}, timeout=2.0)

        h1 = {}
        t1 = threading.Thread(target=_req, args=(h1,))
        t1.start()
        deadline = time.time() + 1.0
        while not sent_old and time.time() < deadline:
            time.sleep(0.01)
        env_old = json.loads(sent_old[0].decode())
        cli_old.dispatch_response({
            'type': 'relay_response',
            'request_id': env_old['request_id'],
            'data': {'tag': 'old'},
        })
        t1.join(timeout=2.0)
        self.assertEqual(h1['r']['data']['tag'], 'old')

        # Swap to new client; pending requests on old should be cancelled
        # by the worker (cancel_all). Future requests go to new.
        cli_old.cancel_all('reconnecting')
        swap.set_inner(cli_new)

        h2 = {}
        t2 = threading.Thread(target=_req, args=(h2,))
        t2.start()
        deadline = time.time() + 1.0
        while not sent_new and time.time() < deadline:
            time.sleep(0.01)
        # Only the new client should have seen the second request.
        self.assertEqual(len(sent_new), 1)
        self.assertEqual(len(sent_old), 1)  # unchanged
        env_new = json.loads(sent_new[0].decode())
        cli_new.dispatch_response({
            'type': 'relay_response',
            'request_id': env_new['request_id'],
            'data': {'tag': 'new'},
        })
        t2.join(timeout=2.0)
        self.assertEqual(h2['r']['data']['tag'], 'new')

    def test_get_inner_reflects_current_state(self):
        swap = SwappableServerFsClient()
        self.assertIsNone(swap.get_inner())
        cli = ServerFsClient(send_callable=lambda b: None)
        swap.set_inner(cli)
        self.assertIs(swap.get_inner(), cli)
        swap.clear_inner()
        self.assertIsNone(swap.get_inner())


if __name__ == '__main__':
    unittest.main()
