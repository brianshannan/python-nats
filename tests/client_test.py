import sys
import unittest

import tornado.httpclient
import tornado.concurrent
import tornado.testing
import tornado.gen
import tornado.ioloop
import tornado.iostream
import tornado.tcpserver
import subprocess
import threading
import tempfile
import time
import json
import socket
import ssl
import os

from datetime import timedelta
from collections import defaultdict as Hash
from nats.io import Client
from nats.io import Client as NATS
from nats.io.errors import *
from nats.io.utils import new_inbox, INBOX_PREFIX
from nats.protocol.parser import *
from nats import __lang__, __version__


class Gnatsd(object):
    def __init__(
            self,
            port=4222,
            user="",
            password="",
            timeout=0,
            http_port=8222,
            config_file=None,
            debug=False,
            conf=None,
            cluster_port=None,
    ):
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.http_port = http_port
        self.cluster_port = cluster_port
        self.proc = None
        self.debug = debug
        self.config_file = config_file
        self.conf = conf
        self.thread = None

        env_debug_flag = os.environ.get("DEBUG_NATS_TEST")
        if env_debug_flag == "true":
            self.debug = True

    def __enter__(self):
        """For when NATS client is used in a context manager"""
        config_file = tempfile.NamedTemporaryFile(mode='w', delete=True)
        self.config_file = config_file
        self.config_file.write(self.conf)
        self.config_file.flush()

        t = threading.Thread(target=self.start)
        self.thread = t
        self.thread.start()

        http = tornado.httpclient.HTTPClient()
        while True:
            try:
                response = http.fetch(
                    'http://127.0.0.1:%d/varz' % self.http_port)
                if response.code == 200:
                    break
                continue
            except:
                time.sleep(0.1)
                continue
        return self

    def __exit__(self, *exc_info):
        """Close connection to NATS when used in a context manager"""
        self.finish()
        self.thread.join()

    def start(self):
        cmd = ["gnatsd"]
        cmd.append("-p")
        cmd.append("%d" % self.port)
        cmd.append("-m")
        cmd.append("%d" % self.http_port)

        if self.cluster_port is not None:
            cmd.append("--cluster")
            cmd.append("nats://127.0.0.1:%d" % self.cluster_port)

        if self.config_file is not None:
            cmd.append("-c")
            cmd.append(self.config_file.name)

        if self.user != "":
            cmd.append("--user")
            cmd.append(self.user)
        if self.password != "":
            cmd.append("--pass")
            cmd.append(self.password)

        if self.debug:
            cmd.append("-DV")

        if self.debug:
            self.proc = subprocess.Popen(cmd)
        else:
            # Redirect to dev null all server output
            devnull = open(os.devnull, 'w')
            self.proc = subprocess.Popen(
                cmd, stdout=devnull, stderr=subprocess.STDOUT)

        if self.debug:
            if self.proc is None:
                print(
                    "[\031[0;33mDEBUG\033[0;0m] Failed to start server listening on port %d started."
                    % self.port)
            else:
                print(
                    "[\033[0;33mDEBUG\033[0;0m] Server listening on port %d started."
                    % self.port)

    def finish(self):
        if self.debug:
            print(
                "[\033[0;33mDEBUG\033[0;0m] Server listening on %d will stop."
                % self.port)

        if self.debug and self.proc is None:
            print(
                "[\033[0;31mDEBUG\033[0;0m] Failed terminating server listening on port %d"
                % self.port)
        else:
            try:
                self.proc.terminate()
                self.proc.wait()
            except Exception as e:
                if self.debug:
                    print(
                        "[\033[0;33m WARN\033[0;0m] Could not stop server listening on %d. (%s)"
                        % (self.port, e))

            if self.debug:
                print(
                    "[\033[0;33mDEBUG\033[0;0m] Server listening on %d was stopped."
                    % self.port)


class Log():
    def __init__(self, debug=False):
        self.records = Hash(list)
        self.debug = debug

    def persist(self, msg):
        if self.debug:
            print(
                "[\033[0;33mDEBUG\033[0;0m] Message received: [{0} {1} {2}].".
                format(msg.subject, msg.reply, msg.data))
        self.records[msg.subject].append(msg)


class ClientUtilsTest(unittest.TestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))

    def test_default_connect_command(self):
        nc = Client()
        nc.options["verbose"] = False
        nc.options["pedantic"] = False
        nc.options["auth_required"] = False
        nc.options["name"] = None
        nc.options["no_echo"] = False
        got = nc.connect_command()
        expected = 'CONNECT {"echo": true, "lang": "python2", "pedantic": false, "protocol": 1, "verbose": false, "version": "%s"}\r\n' % __version__
        self.assertEqual(expected, got)

    def test_default_connect_command_with_name(self):
        nc = Client()
        nc.options["verbose"] = False
        nc.options["pedantic"] = False
        nc.options["auth_required"] = False
        nc.options["name"] = "secret"
        nc.options["no_echo"] = False
        got = nc.connect_command()
        expected = 'CONNECT {"echo": true, "lang": "python2", "name": "secret", "pedantic": false, "protocol": 1, "verbose": false, "version": "%s"}\r\n' % __version__
        self.assertEqual(expected, got)

    def tests_generate_new_inbox(self):
        inbox = new_inbox()
        self.assertTrue(inbox.startswith(INBOX_PREFIX))
        min_expected_len = len(INBOX_PREFIX)
        self.assertTrue(len(inbox) > min_expected_len)


class ClientTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        self.threads = []
        self.server_pool = []

        server = Gnatsd(port=4222)
        self.server_pool.append(server)

        for gnatsd in self.server_pool:
            t = threading.Thread(target=gnatsd.start)
            self.threads.append(t)
            t.start()

        http = tornado.httpclient.HTTPClient()
        while True:
            try:
                response = http.fetch('http://127.0.0.1:8222/varz')
                if response.code == 200:
                    break
                continue
            except:
                time.sleep(0.1)
                continue
        super(ClientTest, self).setUp()

    def tearDown(self):
        for gnatsd in self.server_pool:
            gnatsd.finish()

        for t in self.threads:
            t.join()

        super(ClientTest, self).tearDown()

    @tornado.testing.gen_test
    def test_connect_verbose(self):
        nc = Client()
        options = {"verbose": True, "io_loop": self.io_loop}
        yield nc.connect(**options)

        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        got = nc.connect_command()
        expected = 'CONNECT {"echo": true, "lang": "python2", "pedantic": false, "protocol": 1, "verbose": true, "version": "%s"}\r\n' % __version__
        self.assertEqual(expected, got)

    @tornado.testing.gen_test
    def test_connect_pedantic(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop, pedantic=True)

        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        got = nc.connect_command()
        expected = 'CONNECT {"echo": true, "lang": "python2", "pedantic": true, "protocol": 1, "verbose": false, "version": "%s"}\r\n' % __version__
        self.assertEqual(expected, got)

    @tornado.testing.gen_test
    def test_connect_custom_connect_timeout(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop, connect_timeout=1)
        self.assertEqual(1, nc.options["connect_timeout"])

    @tornado.testing.gen_test
    def test_parse_info(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)

        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)
        self.assertIn("server_id", info_keys)
        self.assertIn("version", info_keys)
        self.assertIn("go", info_keys)
        self.assertIn("host", info_keys)
        self.assertIn("port", info_keys)
        self.assertIn("max_payload", info_keys)
        self.assertIn("client_id", info_keys)

    def test_connect_syntax_sugar(self):
        nc = NATS()
        nc._setup_server_pool(["nats://127.0.0.1:4222", "nats://127.0.0.1:4223", "nats://127.0.0.1:4224"])
        self.assertEqual(3, len(nc._server_pool))

        nc = NATS()
        nc._setup_server_pool("nats://127.0.0.1:4222")
        self.assertEqual(1, len(nc._server_pool))

        nc = NATS()
        nc._setup_server_pool("127.0.0.1:4222")
        self.assertEqual(1, len(nc._server_pool))

        nc = NATS()
        nc._setup_server_pool("nats://127.0.0.1:")
        self.assertEqual(1, len(nc._server_pool))

        nc = NATS()
        nc._setup_server_pool("127.0.0.1")
        self.assertEqual(1, len(nc._server_pool))
        self.assertEqual(4222, nc._server_pool[0].uri.port)

        nc = NATS()
        nc._setup_server_pool("demo.nats.io")
        self.assertEqual(1, len(nc._server_pool))
        self.assertEqual("demo.nats.io", nc._server_pool[0].uri.hostname)
        self.assertEqual(4222, nc._server_pool[0].uri.port)

        nc = NATS()
        nc._setup_server_pool("localhost:")
        self.assertEqual(1, len(nc._server_pool))
        self.assertEqual(4222, nc._server_pool[0].uri.port)

        nc = NATS()
        with self.assertRaises(NatsError):
            nc._setup_server_pool("::")
        self.assertEqual(0, len(nc._server_pool))

        nc = NATS()
        with self.assertRaises(NatsError):
            nc._setup_server_pool("nats://")

        nc = NATS()
        with self.assertRaises(NatsError):
            nc._setup_server_pool("://")
        self.assertEqual(0, len(nc._server_pool))

        nc = NATS()
        with self.assertRaises(NatsError):
            nc._setup_server_pool("")
        self.assertEqual(0, len(nc._server_pool))

        # Auth examples
        nc = NATS()
        nc._setup_server_pool("hello:world@demo.nats.io:4222")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual("hello", uri.username)
        self.assertEqual("world", uri.password)

        nc = NATS()
        nc._setup_server_pool("hello:@demo.nats.io:4222")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual("hello", uri.username)
        self.assertEqual("", uri.password)

        nc = NATS()
        nc._setup_server_pool(":@demo.nats.io:4222")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual("", uri.username)
        self.assertEqual("", uri.password)

        nc = NATS()
        nc._setup_server_pool("@demo.nats.io:4222")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual("", uri.username)
        self.assertEqual(None, uri.password)

        nc = NATS()
        nc._setup_server_pool("@demo.nats.io:")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual(None, uri.username)
        self.assertEqual(None, uri.password)

        nc = NATS()
        nc._setup_server_pool("@demo.nats.io")
        self.assertEqual(1, len(nc._server_pool))
        uri = nc._server_pool[0].uri
        self.assertEqual("demo.nats.io", uri.hostname)
        self.assertEqual(4222, uri.port)
        self.assertEqual("", uri.username)
        self.assertEqual(None, uri.password)

    @tornado.testing.gen_test(timeout=5)
    def test_connect_fails(self):
        class SampleClient():
            def __init__(self):
                self.nc = Client()
                self.disconnected_cb_called = False

            def disconnected_cb(self):
                self.disconnected_cb_called = True

        client = SampleClient()
        with self.assertRaises(ErrNoServers):
            options = {
                "servers": ["nats://127.0.0.1:4223"],
                "close_cb": client.disconnected_cb,
                "allow_reconnect": False,
                "io_loop": self.io_loop
            }
            yield client.nc.connect(**options)
        self.assertFalse(client.disconnected_cb_called)

    @tornado.testing.gen_test(timeout=5)
    def test_connect_fails_allow_reconnect(self):
        class SampleClient():
            def __init__(self):
                self.nc = Client()
                self.disconnected_cb_called = False
                self.closed_cb_called = False

            def disconnected_cb(self):
                self.disconnected_cb_called = True

            def closed_cb(self):
                self.closed_cb_called = True

        client = SampleClient()
        with self.assertRaises(ErrNoServers):
            options = {
                "servers": ["nats://127.0.0.1:4223"],
                "disconnected_cb": client.disconnected_cb,
                "close_cb": client.closed_cb,
                "allow_reconnect": True,
                "io_loop": self.io_loop,
                "max_reconnect_attempts": 2
            }
            yield client.nc.connect(**options)
        self.assertFalse(client.disconnected_cb_called)
        self.assertFalse(client.closed_cb_called)

    @tornado.testing.gen_test(timeout=5)
    def test_reconnect_fail_calls_closed_cb(self):
        class SampleClient():
            def __init__(self):
                self.nc = Client()
                self.disconnected_cb_called = tornado.concurrent.Future()
                self.closed_cb_called = tornado.concurrent.Future()

            def disconnected_cb(self):
                if not self.disconnected_cb_called.done():
                    self.disconnected_cb_called.set_result(True)

            def closed_cb(self):
                if not self.closed_cb_called.done():
                    self.closed_cb_called.set_result(True)

        c = SampleClient()
        options = {
            "servers": ["nats://127.0.0.1:4449"],
            "closed_cb": c.closed_cb,
            "disconnected_cb": c.disconnected_cb,
            "allow_reconnect": True,
            "loop": self.io_loop,
            "max_reconnect_attempts": 2,
            "reconnect_time_wait": 0.1
        }
        with Gnatsd(port=4449, http_port=8449, conf="") as natsd:
            yield c.nc.connect(**options)
            natsd.finish()

            yield tornado.gen.with_timeout(timedelta(seconds=1), c.disconnected_cb_called)
            yield tornado.gen.with_timeout(timedelta(seconds=2), c.closed_cb_called)

    @tornado.testing.gen_test(timeout=5)
    def test_connect_fails_allow_reconnect_forever_until_close(self):
        class SampleClient():
            def __init__(self):
                self.nc = Client()
                self.disconnected_cb_called = False
                self.closed_cb_called = False

            def disconnected_cb(self):
                self.disconnected_cb_called = True

            def close_cb(self):
                self.closed_cb_called = True

        client = SampleClient()
        options = {
            "servers": ["nats://127.0.0.1:4223"],
            "close_cb": client.close_cb,
            "disconnected_cb": client.disconnected_cb,
            "allow_reconnect": True,
            "io_loop": self.io_loop,
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 0.1
        }
        self.io_loop.spawn_callback(client.nc.connect, **options)
        yield tornado.gen.sleep(2)
        yield client.nc.close()
        self.assertTrue(client.nc._server_pool[0].reconnects > 10)
        self.assertTrue(client.disconnected_cb_called)
        self.assertTrue(client.closed_cb_called)

    @tornado.testing.gen_test
    def test_iostream_closed_on_op_error(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)
        self.assertTrue(nc.is_connected)
        self.assertEqual(nc.stats['reconnects'], 0)
        old_io = nc.io

        # Unbind and reconnect.
        yield nc._process_op_err()

        self.assertTrue(nc.is_connected)
        self.assertEqual(nc.stats['reconnects'], 1)
        self.assertTrue(old_io.closed())
        self.assertFalse(nc.io.closed())
        # Unbind, but don't reconnect.
        nc.options["allow_reconnect"] = False

        yield nc._process_op_err()

        self.assertFalse(nc.is_connected)
        self.assertTrue(nc.io.closed())

    @tornado.testing.gen_test
    def test_flusher_exits_on_op_error(self):
        class FlusherClient(Client):
            def __init__(self, *args, **kwargs):
                super(FlusherClient, self).__init__(*args, **kwargs)
                self.flushers_running = {}

            @tornado.gen.coroutine
            def _flusher_loop(self):
                flusher_id = len(self.flushers_running)
                self.flushers_running.update({flusher_id: True})
                yield super(FlusherClient, self)._flusher_loop()
                self.flushers_running.update({flusher_id: False})

        nc = FlusherClient()
        yield nc.connect(io_loop=self.io_loop)
        self.assertTrue(nc.is_connected)
        self.assertEqual(len(nc.flushers_running), 1)
        self.assertTrue(nc.flushers_running[0])
        # Unbind and reconnect.
        yield nc._process_op_err()
        self.assertTrue(nc.is_connected)
        self.assertEqual(len(nc.flushers_running), 2)
        self.assertFalse(nc.flushers_running[0])
        self.assertTrue(nc.flushers_running[1])
        # Unbind, but don't reconnect.
        nc.options["allow_reconnect"] = False
        yield nc._process_op_err()
        yield tornado.gen.sleep(0.1)
        self.assertFalse(nc.is_connected)
        self.assertTrue(nc.io.closed())
        self.assertEqual(len(nc.flushers_running), 2)
        self.assertFalse(nc.flushers_running[0])
        self.assertFalse(nc.flushers_running[1])

    @tornado.testing.gen_test
    def test_subscribe(self):
        nc = Client()
        options = {"io_loop": self.io_loop}
        yield nc.connect(**options)
        self.assertEqual(Client.CONNECTED, nc._status)
        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        inbox = new_inbox()
        yield nc.subscribe("help.1")
        yield nc.subscribe("help.2")
        yield tornado.gen.sleep(0.5)

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/connz' % self.server_pool[0].http_port)
        result = json.loads(response.body)
        connz = result['connections'][0]
        self.assertEqual(2, connz['subscriptions'])

    @tornado.testing.gen_test
    def test_subscribe_sync(self):
        nc = Client()
        msgs = []

        @tornado.gen.coroutine
        def subscription_handler(msg):
            # Futures for subscription are each processed
            # in sequence.
            if msg.subject == "tests.1":
                yield tornado.gen.sleep(1.0)
            if msg.subject == "tests.3":
                yield tornado.gen.sleep(1.0)
            msgs.append(msg)

        yield nc.connect(io_loop=self.io_loop)
        sid = yield nc.subscribe("tests.>", cb=subscription_handler)

        for i in range(0, 5):
            yield nc.publish("tests.{0}".format(i), b'bar')

        # Wait a bit for messages to be received.
        yield tornado.gen.sleep(4.0)
        self.assertEqual(5, len(msgs))
        self.assertEqual("tests.1", msgs[1].subject)
        self.assertEqual("tests.3", msgs[3].subject)
        yield nc.close()

    @tornado.testing.gen_test
    def test_subscribe_sync_non_coro(self):
        nc = Client()
        msgs = []

        def subscription_handler(msg):
            # Callback blocks so dispatched in sequence.
            if msg.subject == "tests.1":
                time.sleep(0.5)
            if msg.subject == "tests.3":
                time.sleep(0.2)
            msgs.append(msg)

        yield nc.connect(io_loop=self.io_loop)
        sid = yield nc.subscribe("tests.>", cb=subscription_handler)

        for i in range(0, 5):
            yield nc.publish("tests.{0}".format(i), b'bar')

        # Wait a bit for messages to be received.
        yield tornado.gen.sleep(4.0)
        self.assertEqual(5, len(msgs))
        self.assertEqual("tests.1", msgs[1].subject)
        self.assertEqual("tests.3", msgs[3].subject)
        yield nc.close()

    @tornado.testing.gen_test
    def test_subscribe_async(self):
        nc = Client()
        msgs = []

        @tornado.gen.coroutine
        def subscription_handler(msg):
            # Callback dispatched asynchronously and a coroutine
            # so it does not block.
            if msg.subject == "tests.1":
                yield tornado.gen.sleep(0.5)
            if msg.subject == "tests.3":
                yield tornado.gen.sleep(0.2)
            msgs.append(msg)

        yield nc.connect(io_loop=self.io_loop)
        sid = yield nc.subscribe_async("tests.>", cb=subscription_handler)

        for i in range(0, 5):
            yield nc.publish("tests.{0}".format(i), b'bar')

        # Wait a bit for messages to be received.
        yield tornado.gen.sleep(4.0)
        self.assertEqual(5, len(msgs))
        self.assertEqual("tests.1", msgs[4].subject)
        self.assertEqual("tests.3", msgs[3].subject)
        yield nc.close()

    @tornado.testing.gen_test
    def test_subscribe_async_non_coro(self):
        nc = Client()
        msgs = []

        def subscription_handler(msg):
            # Dispatched asynchronously but would be received in sequence...
            msgs.append(msg)

        yield nc.connect(io_loop=self.io_loop)
        sid = yield nc.subscribe_async("tests.>", cb=subscription_handler)

        for i in range(0, 5):
            yield nc.publish("tests.{0}".format(i), b'bar')

        # Wait a bit for messages to be received.
        yield tornado.gen.sleep(4.0)
        self.assertEqual(5, len(msgs))
        self.assertEqual("tests.1", msgs[1].subject)
        self.assertEqual("tests.3", msgs[3].subject)
        yield nc.close()

    @tornado.testing.gen_test
    def test_publish(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)
        self.assertEqual(Client.CONNECTED, nc._status)
        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        log = Log()
        yield nc.subscribe(">", "", log.persist)
        yield nc.publish("one", "hello")
        yield nc.publish("two", "world")
        yield tornado.gen.sleep(1.0)

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)
        self.assertEqual(10, varz['in_bytes'])
        self.assertEqual(10, varz['out_bytes'])
        self.assertEqual(2, varz['in_msgs'])
        self.assertEqual(2, varz['out_msgs'])
        self.assertEqual(2, len(log.records.keys()))
        self.assertEqual("hello", log.records['one'][0].data)
        self.assertEqual("world", log.records['two'][0].data)
        self.assertEqual(10, nc.stats['in_bytes'])
        self.assertEqual(10, nc.stats['out_bytes'])
        self.assertEqual(2, nc.stats['in_msgs'])
        self.assertEqual(2, nc.stats['out_msgs'])

    @tornado.testing.gen_test(timeout=15)
    def test_publish_race_condition(self):
        # This tests a race condition fixed in #23 where a series of
        # large publishes followed by a flush and another publish
        # will cause the last publish to never get written.
        nc = Client()

        yield nc.connect(io_loop=self.io_loop)
        self.assertTrue(nc.is_connected)

        @tornado.gen.coroutine
        def sub(msg):
            sub.msgs.append(msg)
            if len(sub.msgs) == 501:
                sub.future.set_result(True)

        sub.msgs = []
        sub.future = tornado.concurrent.Future()
        yield nc.subscribe("help.*", cb=sub)

        # Close to 1MB payload
        payload = "A" * 1000000

        # Publish messages from 0..499
        for i in range(500):
            yield nc.publish("help.%s" % i, payload)

        # Relinquish control often to unblock the flusher
        yield tornado.gen.sleep(0)
        yield nc.publish("help.500", "A")

        # Wait for the future to yield after receiving all the messages.
        try:
            yield tornado.gen.with_timeout(timedelta(seconds=10), sub.future)
        except:
            # Skip timeout in case it may occur and let test fail
            # when checking how many messages we received in the end.
            pass

        # We should definitely have all the messages
        self.assertEqual(len(sub.msgs), 501)

        for i in range(501):
            self.assertEqual(sub.msgs[i].subject, u"help.%s" % (i))

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)

        self.assertEqual(500000001, varz['in_bytes'])
        self.assertEqual(500000001, varz['out_bytes'])
        self.assertEqual(501, varz['in_msgs'])
        self.assertEqual(501, varz['out_msgs'])
        self.assertEqual(500000001, nc.stats['in_bytes'])
        self.assertEqual(500000001, nc.stats['out_bytes'])
        self.assertEqual(501, nc.stats['in_msgs'])
        self.assertEqual(501, nc.stats['out_msgs'])

    @tornado.testing.gen_test(timeout=15)
    def test_publish_flush_race_condition(self):
        # This tests a race condition fixed in #23 where a series of
        # large publishes followed by a flush and another publish
        # will cause the last publish to never get written.
        nc = Client()

        yield nc.connect(io_loop=self.io_loop)
        self.assertTrue(nc.is_connected)

        @tornado.gen.coroutine
        def sub(msg):
            sub.msgs.append(msg)
            if len(sub.msgs) == 501:
                sub.future.set_result(True)

        sub.msgs = []
        sub.future = tornado.concurrent.Future()
        yield nc.subscribe("help.*", cb=sub)

        # Close to 1MB payload
        payload = "A" * 1000000

        # Publish messages from 0..499
        for i in range(500):
            yield nc.publish("help.%s" % i, payload)
            if i % 10 == 0:
                # Relinquish control often to unblock the flusher
                yield tornado.gen.sleep(0)

        yield nc.publish("help.500", "A")

        # Flushing and doing ping/pong should not cause commands
        # to be dropped either.
        yield nc.flush()

        # Wait for the future to yield after receiving all the messages.
        try:
            yield tornado.gen.with_timeout(timedelta(seconds=10), sub.future)
        except:
            # Skip timeout in case it may occur and let test fail
            # when checking how many messages we received in the end.
            pass

        # We should definitely have all the messages
        self.assertEqual(len(sub.msgs), 501)

        for i in range(501):
            self.assertEqual(sub.msgs[i].subject, u"help.%s" % (i))

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)

        self.assertEqual(500000001, varz['in_bytes'])
        self.assertEqual(500000001, varz['out_bytes'])
        self.assertEqual(501, varz['in_msgs'])
        self.assertEqual(501, varz['out_msgs'])
        self.assertEqual(500000001, nc.stats['in_bytes'])
        self.assertEqual(500000001, nc.stats['out_bytes'])
        self.assertEqual(501, nc.stats['in_msgs'])
        self.assertEqual(501, nc.stats['out_msgs'])

    @tornado.testing.gen_test
    def test_unsubscribe(self):
        nc = Client()
        options = {"io_loop": self.io_loop}
        yield nc.connect(**options)

        log = Log()
        sid = yield nc.subscribe("foo", cb=log.persist)
        yield nc.publish("foo", b'A')
        yield nc.publish("foo", b'B')
        yield tornado.gen.sleep(1)

        sub = nc._subs[sid]
        yield nc.unsubscribe(sid)
        yield nc.flush()
        self.assertEqual(sub.closed, True)

        yield nc.publish("foo", b'C')
        yield nc.publish("foo", b'D')
        self.assertEqual(2, len(log.records["foo"]))

        self.assertEqual(b'A', log.records["foo"][0].data)
        self.assertEqual(b'B', log.records["foo"][1].data)

        # Should not exist by now
        with self.assertRaises(KeyError):
            nc._subs[sid].received

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/connz' % self.server_pool[0].http_port)
        result = json.loads(response.body)
        connz = result['connections'][0]
        self.assertEqual(0, connz['subscriptions'])

    @tornado.testing.gen_test
    def test_unsubscribe_only_if_max_reached(self):
        nc = Client()
        options = {"io_loop": self.io_loop}
        yield nc.connect(**options)

        log = Log()
        sid = yield nc.subscribe("foo", cb=log.persist)
        yield nc.publish("foo", b'A')
        yield nc.publish("foo", b'B')
        yield nc.publish("foo", b'C')
        yield tornado.gen.sleep(1)
        self.assertEqual(3, len(log.records["foo"]))

        sub = nc._subs[sid]
        yield nc.unsubscribe(sid, 3)
        self.assertEqual(sub.closed, True)

        yield nc.publish("foo", b'D')
        yield nc.flush()
        self.assertEqual(3, len(log.records["foo"]))

        self.assertEqual(b'A', log.records["foo"][0].data)
        self.assertEqual(b'B', log.records["foo"][1].data)
        self.assertEqual(b'C', log.records["foo"][2].data)

        # Should not exist by now
        yield tornado.gen.sleep(1)
        with self.assertRaises(KeyError):
            nc._subs[sid].received

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/connz' % self.server_pool[0].http_port)
        result = json.loads(response.body)
        connz = result['connections'][0]
        self.assertEqual(0, connz['subscriptions'])

    @tornado.testing.gen_test
    def test_request(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)

        class Component:
            def __init__(self, nc):
                self.nc = nc
                self.replies = []

            @tornado.gen.coroutine
            def receive_responses(self, msg=None):
                self.replies.append(msg)

            @tornado.gen.coroutine
            def respond(self, msg=None):
                yield self.nc.publish(msg.reply, "ok:1")
                yield self.nc.publish(msg.reply, "ok:2")
                yield self.nc.publish(msg.reply, "ok:3")

        log = Log()
        c = Component(nc)
        yield nc.subscribe(">", "", log.persist)
        yield nc.subscribe("help", "", c.respond)
        yield nc.request("help", "please", expected=2, cb=c.receive_responses)

        subs = []
        for _, sub in nc._subs.items():
            subs.append(sub)
        self.assertEqual(len(subs), 3)
        yield tornado.gen.sleep(0.5)
        self.assertEqual(len(self.io_loop._callbacks), 0)

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)
        self.assertEqual(18, varz['in_bytes'])
        self.assertEqual(32, varz['out_bytes'])
        self.assertEqual(4, varz['in_msgs'])
        self.assertEqual(7, varz['out_msgs'])
        self.assertEqual(2, len(log.records.keys()))
        self.assertEqual("please", log.records['help'][0].data)
        self.assertEqual(2, len(c.replies))
        self.assertEqual(32, nc.stats['in_bytes'])
        self.assertEqual(18, nc.stats['out_bytes'])
        self.assertEqual(7, nc.stats['in_msgs'])
        self.assertEqual(4, nc.stats['out_msgs'])

        full_msg = ''
        for msg in log.records['help']:
            full_msg += msg.data

        self.assertEqual('please', full_msg)
        self.assertEqual("ok:1", c.replies[0].data)
        self.assertEqual("ok:2", c.replies[1].data)
        yield nc.close()

    @tornado.testing.gen_test
    def test_timed_request(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)

        class Component:
            def __init__(self, nc):
                self.nc = nc

            @tornado.gen.coroutine
            def respond(self, msg=None):
                yield self.nc.publish(msg.reply, "ok:1")
                yield self.nc.publish(msg.reply, "ok:2")
                yield self.nc.publish(msg.reply, "ok:3")

        log = Log()
        c = Component(nc)
        yield nc.subscribe(">", "", log.persist)
        yield nc.subscribe("help", "", c.respond)

        reply = yield nc.timed_request("help", "please")
        self.assertEqual("ok:1", reply.data)

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)
        self.assertEqual(18, varz['in_bytes'])
        self.assertEqual(28, varz['out_bytes'])
        self.assertEqual(4, varz['in_msgs'])
        self.assertEqual(6, varz['out_msgs'])
        self.assertEqual(2, len(log.records.keys()))
        self.assertEqual("please", log.records['help'][0].data)
        self.assertEqual(28, nc.stats['in_bytes'])
        self.assertEqual(18, nc.stats['out_bytes'])
        self.assertEqual(6, nc.stats['in_msgs'])
        self.assertEqual(4, nc.stats['out_msgs'])

        full_msg = ''
        for msg in log.records['help']:
            full_msg += msg.data
        self.assertEqual('please', full_msg)

        # There should not be lingering inboxes with requests by default
        self.assertEqual(len(c.nc._subs), 2)

    @tornado.testing.gen_test
    def test_publish_max_payload(self):
        nc = Client()
        yield nc.connect(io_loop=self.io_loop)
        self.assertEqual(Client.CONNECTED, nc._status)
        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        with self.assertRaises(ErrMaxPayload):
            yield nc.publish("large-message",
                             "A" * (nc._server_info["max_payload"] * 2))

    @tornado.testing.gen_test
    def test_publish_request(self):
        nc = Client()

        yield nc.connect(io_loop=self.io_loop)
        self.assertEqual(Client.CONNECTED, nc._status)
        info_keys = nc._server_info.keys()
        self.assertTrue(len(info_keys) > 0)

        inbox = new_inbox()
        yield nc.publish_request("help.1", inbox, "hello")
        yield nc.publish_request("help.2", inbox, "world")
        yield tornado.gen.sleep(1.0)

        http = tornado.httpclient.AsyncHTTPClient()
        response = yield http.fetch(
            'http://127.0.0.1:%d/varz' % self.server_pool[0].http_port)
        varz = json.loads(response.body)

        self.assertEqual(10, varz['in_bytes'])
        self.assertEqual(0, varz['out_bytes'])
        self.assertEqual(2, varz['in_msgs'])
        self.assertEqual(0, varz['out_msgs'])
        self.assertEqual(0, nc.stats['in_bytes'])
        self.assertEqual(10, nc.stats['out_bytes'])
        self.assertEqual(0, nc.stats['in_msgs'])
        self.assertEqual(2, nc.stats['out_msgs'])

    @tornado.testing.gen_test
    def test_customize_io_buffers(self):
        class Component():
            def __init__(self):
                self.nc = Client()
                self.errors = []
                self.disconnected_cb_called = 0
                self.closed_cb_called = 0

            def error_cb(self, e):
                self.errors.append(e)

            def close_cb(self):
                self.closed_cb_called += 1

            def disconnected_cb(self):
                self.disconnected_cb_called += 1

        c = Component()
        options = {
            "io_loop": self.io_loop,
            "max_read_buffer_size": 1024,
            "max_write_buffer_size": 50,
            "read_chunk_size": 10,
            "error_cb": c.error_cb,
            "close_cb": c.close_cb,
            "disconnected_cb": c.disconnected_cb,
            "max_reconnect_attempts": 1,
        }
        with self.assertRaises(ErrNoServers):
            yield c.nc.connect(**options)
        self.assertEqual(
            tornado.iostream.StreamBufferFullError, c.nc.last_error().__class__)
        self.assertFalse(c.nc.is_connected)
        self.assertEqual(1024, c.nc._max_read_buffer_size)
        self.assertEqual(50, c.nc._max_write_buffer_size)
        self.assertEqual(10, c.nc._read_chunk_size)

    @tornado.testing.gen_test
    def test_default_ping_interval(self):
        class Parser():
            def __init__(self, nc, t):
                self.nc = nc
                self.t = t

            @tornado.gen.coroutine
            def parse(self, data=''):
                self.t.assertEqual(1, len(self.nc._pongs))
                yield self.nc._process_pong()
                self.t.assertEqual(0, len(self.nc._pongs))

        nc = Client()
        nc._ps = Parser(nc, self)
        yield nc.connect(io_loop=self.io_loop)
        yield tornado.gen.sleep(1)
        self.assertEqual(0, nc._pings_outstanding)
        self.assertTrue(nc.is_connected)

    @tornado.testing.gen_test
    def test_custom_ping_interval(self):
        # Wait to be disconnected due to ignoring pings.
        disconnected = tornado.concurrent.Future()

        class Parser():
            def __init__(self, nc):
                self.nc = nc
                self.pongs = []

            @tornado.gen.coroutine
            def parse(self, data=''):
                if b'PONG' in data:
                    self.pongs.append(data)
                    yield self.nc._process_pong()

        def disconnected_cb():
            if not disconnected.done():
                disconnected.set_result(True)

        nc = NATS()
        nc._ps = Parser(nc)
        yield nc.connect(
            loop=self.io_loop,
            ping_interval=0.1,
            max_outstanding_pings=10,
            disconnected_cb=disconnected_cb,
            )
        yield tornado.gen.with_timeout(timedelta(seconds=5), disconnected)
        self.assertTrue(len(nc._ps.pongs) > 5)
        yield nc.close()

    @tornado.testing.gen_test
    def test_ping_slow_replies(self):
        pongs = []

        class Parser():
            def __init__(self, nc):
                self.nc = nc

            @tornado.gen.coroutine
            def parse(self, data=''):
                pongs.append(data)  # but, don't process now

        nc = Client()
        nc._ps = Parser(nc)
        yield nc.connect(
            io_loop=self.io_loop, ping_interval=0.1, max_outstanding_pings=20)
        yield tornado.gen.sleep(1)

        # Should have received more than 5 pongs, but processed none.
        self.assertTrue(len(pongs) > 5)
        self.assertTrue(len(pongs) <= nc._pings_outstanding)
        self.assertEqual(0, nc._pongs_received)
        self.assertEqual(len(nc._pongs), nc._pings_outstanding)
        # Process all that were sent.
        expected_outstanding = nc._pings_outstanding
        for i in range(nc._pings_outstanding):
            yield nc._process_pong()
            expected_outstanding -= 1
            self.assertEqual(expected_outstanding, nc._pings_outstanding)
            self.assertEqual(expected_outstanding, len(nc._pongs))
            self.assertEqual(i + 1, nc._pongs_received)

    @tornado.testing.gen_test
    def test_flush_timeout(self):
        class Parser():
            def __init__(self, nc, t):
                self.nc = nc
                self.t = t

            @tornado.gen.coroutine
            def parse(self, data=''):
                yield tornado.gen.sleep(2.0)
                yield self.nc._process_pong()

        nc = Client()
        nc._ps = Parser(nc, self)
        yield nc.connect(io_loop=self.io_loop)
        with self.assertRaises(tornado.gen.TimeoutError):
            yield nc.flush(timeout=1)
        self.assertEqual(0, len(nc._pongs))

    @tornado.testing.gen_test(timeout=15)
    def test_flush_timeout_lost_message(self):
        class Parser():
            def __init__(self, nc):
                self.nc = nc
                self.drop_messages = False

            @tornado.gen.coroutine
            def parse(self, data=''):
                if not self.drop_messages:
                    yield self.nc._process_pong()

        nc = Client()
        nc._ps = Parser(nc)
        yield nc.connect(loop=self.io_loop)
        nc._ps.drop_messages = True
        with self.assertRaises(tornado.gen.TimeoutError):
            yield nc.flush(timeout=1)
        self.assertEqual(0, len(nc._pongs))
        self.assertEqual(0, nc._pings_outstanding)
        self.assertEqual(0, nc._pongs_received)

        # Successful flush must clear timed out pong and the new one.
        nc._ps.drop_messages = False
        try:
            yield nc.flush(timeout=1)
        finally:
            self.assertEqual(0, len(nc._pongs))
            self.assertEqual(0, nc._pings_outstanding)
            self.assertEqual(1, nc._pongs_received)

    @tornado.testing.gen_test
    def test_timed_request_timeout(self):
        class Parser():
            def __init__(self, nc, t):
                self.nc = nc
                self.t = t

            def parse(self, data=''):
                self.nc._process_pong()

        nc = Client()
        nc._ps = Parser(nc, self)
        yield nc.connect(io_loop=self.io_loop)
        with self.assertRaises(tornado.gen.TimeoutError):
            yield nc.timed_request("hello", "world", timeout=0.5)

    @tornado.testing.gen_test
    def test_process_message_subscription_not_present(self):
        nc = Client()
        yield nc._process_msg(387, 'some-subject', 'some-reply', [0, 1, 2])

    @tornado.testing.gen_test
    def test_subscribe_async_process_messages_concurrently(self):
        nc = Client()

        yield nc.connect(io_loop=self.io_loop)

        @tornado.gen.coroutine
        def sub_foo_handler(msg):
            msgs = sub_foo_handler.msgs
            msgs.append(msg)

            # Should not block other subscriptions processing
            # the messages in parallel...
            yield tornado.gen.sleep(1)

        sub_foo_handler.msgs = []
        yield nc.subscribe("foo", cb=sub_foo_handler)

        @tornado.gen.coroutine
        def sub_bar_handler(msg):
            nc = sub_bar_handler.nc
            msgs = sub_bar_handler.msgs
            msgs.append(msg)
            yield nc.publish(msg.reply, "OK!")

        sub_bar_handler.nc = nc
        sub_bar_handler.msgs = []
        yield nc.subscribe("bar", cb=sub_bar_handler)

        @tornado.gen.coroutine
        def sub_quux_handler(msg):
            msgs = sub_quux_handler.msgs
            msgs.append(msg)

        sub_quux_handler.msgs = []
        yield nc.subscribe("quux", cb=sub_quux_handler)

        yield nc.publish("foo", "hello")
        for i in range(0, 10):
            yield nc.publish("quux", "test-{}".format(i))

        response = yield nc.request("bar", b'help')
        self.assertEqual(response.data, 'OK!')

        yield tornado.gen.sleep(0.2)
        self.assertEqual(len(sub_foo_handler.msgs), 1)
        self.assertEqual(len(sub_bar_handler.msgs), 1)
        self.assertEqual(len(sub_quux_handler.msgs), 10)

        yield nc.close()

    @tornado.testing.gen_test
    def test_subscribe_slow_consumer_pending_msgs_limit(self):
        nc = Client()

        def error_cb(err):
            error_cb.errors.append(err)

        error_cb.errors = []

        yield nc.connect(io_loop=self.io_loop, error_cb=error_cb)

        @tornado.gen.coroutine
        def sub_hello_handler(msg):
            msgs = sub_hello_handler.msgs
            msgs.append(msg)

            if len(msgs) == 5:
                yield tornado.gen.sleep(0.5)

        sub_hello_handler.msgs = []
        yield nc.subscribe("hello", cb=sub_hello_handler, pending_msgs_limit=5)

        for i in range(0, 20):
            yield nc.publish("hello", "test-{}".format(i))
        yield nc.flush(1)

        # Wait a bit for subscriber to recover
        yield tornado.gen.sleep(0.5)

        for i in range(0, 3):
            yield nc.publish("hello", "ok-{}".format(i))
        yield nc.flush(1)

        # Wait a bit to receive the final messages
        yield tornado.gen.sleep(0.5)

        # There would be a few async slow consumer errors
        errors = error_cb.errors
        self.assertTrue(len(errors) > 0)
        self.assertTrue(type(errors[0]) is ErrSlowConsumer)

        # We should have received some messages and dropped others,
        # but definitely got the last 3 messages after recovering
        # from the slow consumer error.
        msgs = sub_hello_handler.msgs
        self.assertEqual(len(msgs), 13)

        msgs = sub_hello_handler.msgs[-3:]
        for i in range(0, 3):
            self.assertEqual("ok-{}".format(i), msgs[i].data)
        yield nc.close()

    @tornado.testing.gen_test
    def test_subscribe_slow_consumer_pending_bytes_limit(self):
        nc = Client()

        def error_cb(err):
            error_cb.errors.append(err)

        error_cb.errors = []

        yield nc.connect(io_loop=self.io_loop, error_cb=error_cb)

        @tornado.gen.coroutine
        def sub_hello_handler(msg):
            msgs = sub_hello_handler.msgs
            msgs.append(msg)
            sub_hello_handler.data += msg.data
            if len(sub_hello_handler.data) == 10:
                yield tornado.gen.sleep(0.5)

        sub_hello_handler.msgs = []
        sub_hello_handler.data = ''
        yield nc.subscribe(
            "hello", cb=sub_hello_handler, pending_bytes_limit=10)

        for i in range(0, 20):
            yield nc.publish("hello", "A")
        yield nc.flush(1)

        # Wait a bit for subscriber to recover
        yield tornado.gen.sleep(1)

        for i in range(0, 3):
            yield nc.publish("hello", "B")
        yield nc.flush(1)

        # Wait a bit to receive the final messages
        yield tornado.gen.sleep(1)

        # There would be a few async slow consumer errors
        errors = error_cb.errors
        self.assertTrue(len(errors) > 0)
        self.assertTrue(type(errors[0]) is ErrSlowConsumer)

        # We should have received some messages and dropped others,
        # but definitely got the last 3 messages after recovering
        # from the slow consumer error.
        msgs = sub_hello_handler.msgs
        self.assertTrue(len(msgs) > 10 and len(msgs) != 23)

        msgs = sub_hello_handler.msgs[-3:]
        for i in range(0, 3):
            self.assertEqual("B", msgs[i].data)
        yield nc.close()

    @tornado.testing.gen_test
    def test_close_stops_subscriptions_loops(self):
        nc = Client()

        def error_cb(err):
            error_cb.errors.append(err)

        error_cb.errors = []

        yield nc.connect(io_loop=self.io_loop, error_cb=error_cb)

        @tornado.gen.coroutine
        def sub_hello_handler(msg):
            msgs = sub_hello_handler.msgs
            msgs.append(msg)

        sub_hello_handler.msgs = []
        yield nc.subscribe("hello.foo.bar", cb=sub_hello_handler)
        yield nc.subscribe("hello.*.*", cb=sub_hello_handler)
        yield nc.subscribe("hello.>", cb=sub_hello_handler)
        yield nc.subscribe(">", cb=sub_hello_handler)

        for i in range(0, 10):
            yield nc.publish("hello.foo.bar", "test-{}".format(i))
        yield nc.flush(1)
        msgs = sub_hello_handler.msgs
        self.assertEqual(len(msgs), 40)

        self.assertEqual(len(nc._subs), 4)

        subs = []
        for _, sub in nc._subs.items():
            subs.append(sub)
            self.assertEqual(sub.closed, False)

        yield nc.close()

        # Close should have removed all subscriptions
        self.assertEqual(len(nc._subs), 0)

        # Let background message processors stop
        yield tornado.gen.sleep(0)
        self.assertEqual(len(self.io_loop._callbacks), 0)

        for sub in subs:
            self.assertEqual(sub.closed, True)

    @tornado.testing.gen_test(timeout=10)
    def test_subscribe_no_echo(self):
        nc = NATS()
        msgs = []

        nc2 = NATS()
        msgs2 = []

        @tornado.gen.coroutine
        def subscription_handler(msg):
            msgs.append(msg)

        @tornado.gen.coroutine
        def subscription_handler2(msg):
            msgs2.append(msg)

        yield nc.connect(loop=self.io_loop, no_echo=True)
        sid = yield nc.subscribe("foo", cb=subscription_handler)
        yield nc.flush()

        yield nc2.connect(loop=self.io_loop, no_echo=False)
        sid2 = yield nc2.subscribe("foo", cb=subscription_handler2)
        yield nc2.flush()

        payload = b'hello world'
        for i in range(0, 10):
            yield nc.publish("foo", payload)
        yield nc.flush()

        # Wait a bit for message to be received.
        yield tornado.gen.sleep(0.5)

        self.assertEqual(0, len(msgs))
        self.assertEqual(10, len(msgs2))
        self.assertEqual(0, nc._subs[sid].received)
        self.assertEqual(10, nc2._subs[sid].received)
        yield nc.close()
        yield nc2.close()
        self.assertEqual(0,  nc.stats['in_msgs'])
        self.assertEqual(0, nc.stats['in_bytes'])
        self.assertEqual(10,  nc.stats['out_msgs'])
        self.assertEqual(110, nc.stats['out_bytes'])
        self.assertEqual(10,  nc2.stats['in_msgs'])
        self.assertEqual(110, nc2.stats['in_bytes'])
        self.assertEqual(0,  nc2.stats['out_msgs'])
        self.assertEqual(0, nc2.stats['out_bytes'])

class ClientAuthTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        self.threads = []
        self.server_pool = []

        server1 = Gnatsd(port=4223, user="foo", password="bar", http_port=8223)
        server2 = Gnatsd(port=4224, user="hoge", password="fuga", http_port=8224)
        self.server_pool.append(server1)
        self.server_pool.append(server2)

        for gnatsd in self.server_pool:
            t = threading.Thread(target=gnatsd.start)
            self.threads.append(t)
            t.start()

        http = tornado.httpclient.HTTPClient()
        while True:
            try:
                response1 = http.fetch('http://127.0.0.1:8223/varz')
                response2 = http.fetch('http://127.0.0.1:8224/varz')
                if response1.code == 200 and response2.code == 200:
                    break
                continue
            except:
                time.sleep(0.1)
                continue
        super(ClientAuthTest, self).setUp()

    def tearDown(self):
        super(ClientAuthTest, self).tearDown()
        for gnatsd in self.server_pool:
            gnatsd.finish()

        for t in self.threads:
            t.join()

    @tornado.testing.gen_test(timeout=10)
    def test_auth_connect(self):
        class SampleClient():
            def __init__(self):
                self.nc = Client()
                self.errors = []
                self.disconnected_future = tornado.concurrent.Future()
                self.reconnected_future = tornado.concurrent.Future()
                self.closed_future = tornado.concurrent.Future()

            @tornado.gen.coroutine
            def foo(self, msg):
                yield self.nc.publish(msg.reply, "OK:{}:{}".format(msg.subject, msg.data))

            @tornado.gen.coroutine
            def bar(self, msg):
                yield self.nc.publish(msg.reply, "OK:{}:{}".format(msg.subject, msg.data))

            @tornado.gen.coroutine
            def quux(self, msg):
                yield self.nc.publish(msg.reply, "OK:{}:{}".format(msg.subject, msg.data))

            def error_cb(self, err):
                self.errors.append(err)

            def disconnected_cb(self):
                if not self.disconnected_future.done():
                    self.disconnected_future.set_result(True)

            def reconnected_cb(self):
                if not self.reconnected_future.done():
                    self.reconnected_future.set_result(True)

            def closed_cb(self):
                if not self.closed_future.done():
                    self.closed_future.set_result(True)

        c = SampleClient()
        options = {
            "dont_randomize": True,
            "servers": [
                "nats://foo:bar@127.0.0.1:4223",
                "nats://hoge:fuga@127.0.0.1:4224"
            ],
            "loop": self.io_loop,
            "error_cb": c.error_cb,
            "reconnected_cb": c.reconnected_cb,
            "closed_cb": c.closed_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnect_time_wait": 0.1,
            "max_reconnect_attempts": 3,
        }
        yield c.nc.connect(**options)
        self.assertEqual(True, c.nc._server_info["auth_required"])

        sid_1 = yield c.nc.subscribe("foo", "", c.foo)
        sid_2 = yield c.nc.subscribe("bar", "", c.bar)
        sid_3 = yield c.nc.subscribe("quux", "", c.quux)
        self.assertEqual(sid_1, 1)
        self.assertEqual(sid_2, 2)
        self.assertEqual(sid_3, 3)
        yield c.nc.flush()

        msg = yield c.nc.request("foo", b"hello")
        self.assertEqual(msg.data, "OK:foo:hello")

        msg = yield c.nc.request("bar", b"hello")
        self.assertEqual(msg.data, "OK:bar:hello")

        msg = yield c.nc.request("quux", b"hello")
        self.assertEqual(msg.data, "OK:quux:hello")

        # Trigger reconnect
        a = c.nc._current_server
        orig_gnatsd = self.server_pool.pop(0)
        orig_gnatsd.finish()
        yield tornado.gen.sleep(1)

        # Use future for when disconnect/reconnect events to happen.
        try:
            yield tornado.gen.with_timeout(timedelta(seconds=2), c.disconnected_future)
            yield tornado.gen.with_timeout(timedelta(seconds=2), c.reconnected_future)
        finally:
            b = c.nc._current_server
            self.assertNotEqual(a.uri, b.uri)

        # Should still be able to request/response after reconnect.
        response = yield c.nc.request("foo", b"world")
        self.assertEqual(response.data, "OK:foo:world")

        response = yield c.nc.request("bar", b"world")
        self.assertEqual(response.data, "OK:bar:world")

        response = yield c.nc.request("quux", b"world")
        self.assertEqual(response.data, "OK:quux:world")

        self.assertTrue(c.nc.is_connected)
        self.assertFalse(c.nc.is_reconnecting)
        self.assertFalse(c.nc.is_closed)

        # Start original server with different auth and should eventually closed connection.
        conf = """
        port = 4223

        http = 8223

        authorization {
          user = hoge
          pass = fuga
        }
        """
        with Gnatsd(port=4223, http_port=8223, conf=conf) as gnatsd:
            # Reset futures before closing.
            c.disconnected_future = tornado.concurrent.Future()

            other_gnatsd = self.server_pool.pop(0)
            other_gnatsd.finish()

            # Reconnect once again
            yield tornado.gen.with_timeout(timedelta(seconds=2), c.disconnected_future)
            yield tornado.gen.with_timeout(timedelta(seconds=2), c.closed_future)

            # There will be a mix of Authorization errors and StreamClosedError errors.
            self.assertTrue(c.errors > 1)

    @tornado.testing.gen_test(timeout=10)
    def test_auth_connect_fails(self):
        class Component:
            def __init__(self, nc):
                self.nc = nc
                self.errors = []
                self.disconnected_cb_called = tornado.concurrent.Future()
                self.closed_cb_called = tornado.concurrent.Future()
                self.reconnected_cb_called = False
                self.log = Log()

            def error_cb(self, err):
                self.errors.append(err)

            def disconnected_cb(self):
                if not self.disconnected_cb_called.done():
                    self.disconnected_cb_called.set_result(True)

            def close_cb(self):
                if not self.closed_cb_called.done():
                    self.closed_cb_called.set_result(True)

            def reconnected_cb(self):
                self.reconnected_cb_called = True

        nc = Client()
        c = Component(nc)

        conf = """

        port = 4228

        http = 8448

        authorization {
          user = foo
          pass = bar
        }

        """
        with Gnatsd(port=4228, http_port=8448, conf=conf) as gnatsd:
            yield c.nc.connect(
                loop=self.io_loop,
                dont_randomize=True,
                servers=[
                    "nats://foo:bar@127.0.0.1:4228",
                    "nats://foo2:bar2@127.0.0.1:4224"
                    ],
                closed_cb=c.close_cb,
                error_cb=c.error_cb,
                disconnected_cb=c.disconnected_cb,
                reconnected_cb=c.reconnected_cb,
                max_reconnect_attempts=2,
                reconnect_time_wait=0.1,
                )
            self.assertEqual(True, c.nc.is_connected)
            self.assertEqual(True, nc._server_info["auth_required"])

            # Confirm that messages went through
            yield c.nc.subscribe("foo", "", c.log.persist)
            yield c.nc.flush()
            yield c.nc.publish("foo", "bar")
            yield c.nc.flush()
            yield tornado.gen.sleep(0.5)

            # Shutdown first server, triggering reconnect...
            gnatsd.finish()

            # Wait for reconnect logic kick in and then fail due to authorization error.
            yield tornado.gen.with_timeout(
                timedelta(seconds=1), c.disconnected_cb_called)
            yield tornado.gen.with_timeout(
                timedelta(seconds=1), c.closed_cb_called)
            errors_at_close = len(c.errors)

            for i in range(0, 20):
                yield tornado.gen.sleep(0.1)
            errors_after_close = len(c.errors)

            self.assertEqual(errors_at_close, errors_after_close)
            self.assertEqual(1, len(c.log.records["foo"]))

    @tornado.testing.gen_test(timeout=10)
    def test_connect_with_auth_token_option(self):
        nc = NATS()

        conf = """
        port = 4227

        http = 8227

        authorization {
          token = token
        }
        """
        with Gnatsd(port=4227, http_port=8227, conf=conf) as gnatsd:
            yield nc.connect("nats://127.0.0.1:4227",
                             token='token',
                             loop=self.io_loop,
                            )
            self.assertIn('auth_required', nc._server_info)
            self.assertTrue(nc.is_connected)

            received = tornado.concurrent.Future()

            @tornado.gen.coroutine
            def handler(msg):
                received.set_result(msg)

            yield nc.subscribe("foo", cb=handler)
            yield nc.flush()
            yield nc.publish("foo", b'bar')

            yield tornado.gen.with_timeout(
                timedelta(seconds=1), received)
            
            yield nc.close()
            self.assertTrue(nc.is_closed)
            self.assertFalse(nc.is_connected)

    @tornado.testing.gen_test(timeout=10)
    def test_close_connection(self):
        nc = Client()
        options = {
            "dont_randomize":
            True,
            "servers": [
                "nats://foo:bar@127.0.0.1:4223",
                "nats://hoge:fuga@127.0.0.1:4224"
            ],
            "io_loop":
            self.io_loop
        }
        yield nc.connect(**options)
        self.assertEqual(True, nc._server_info["auth_required"])

        log = Log()
        sid_1 = yield nc.subscribe("foo", "", log.persist)
        self.assertEqual(sid_1, 1)
        sid_2 = yield nc.subscribe("bar", "", log.persist)
        self.assertEqual(sid_2, 2)
        sid_3 = yield nc.subscribe("quux", "", log.persist)
        self.assertEqual(sid_3, 3)
        yield nc.publish("foo", "hello")
        yield tornado.gen.sleep(1.0)

        # Done
        yield nc.close()

        orig_gnatsd = self.server_pool.pop(0)
        orig_gnatsd.finish()

        try:
            a = nc._current_server
            # Wait and assert that we don't reconnect.
            yield tornado.gen.sleep(3)
        finally:
            b = nc._current_server
            self.assertEqual(a.uri, b.uri)

        self.assertFalse(nc.is_connected)
        self.assertFalse(nc.is_reconnecting)
        self.assertTrue(nc.is_closed)

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.publish("hello", "world")

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.flush()

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.subscribe("hello", "worker")

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.publish_request("hello", "inbox", "world")

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.request("hello", "world")

        with (self.assertRaises(ErrConnectionClosed)):
            yield nc.timed_request("hello", "world")

class ClientTLSTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        self.threads = []
        self.server_pool = []

        conf = """
          # Simple TLS config file
          port: 4444
          net: 127.0.0.1

          http_port: 8222
          tls {
            cert_file: './tests/configs/certs/server-cert.pem'
            key_file:  './tests/configs/certs/server-key.pem'
            ca_file:   './tests/configs/certs/ca.pem'
            timeout:   10
          }
          """
        config_file = tempfile.NamedTemporaryFile(mode='w', delete=True)
        config_file.write(conf)
        config_file.flush()

        server = Gnatsd(port=4444, http_port=8222, config_file=config_file)
        self.server_pool.append(server)
        server = Gnatsd(port=4445, http_port=8223, config_file=config_file)
        self.server_pool.append(server)

        for gnatsd in self.server_pool:
            t = threading.Thread(target=gnatsd.start)
            self.threads.append(t)
            t.start()

            http = tornado.httpclient.HTTPClient()
            while True:
                try:
                    response = http.fetch(
                        'http://127.0.0.1:%d/varz' % gnatsd.http_port)
                    if response.code == 200:
                        break
                    continue
                except:
                    time.sleep(0.1)
                    continue
        super(ClientTLSTest, self).setUp()

    def tearDown(self):
        for gnatsd in self.server_pool:
            gnatsd.finish()

        for t in self.threads:
            t.join()

        super(ClientTLSTest, self).tearDown()

    @tornado.testing.gen_test(timeout=10)
    def test_tls_connection(self):
        class Component:
            def __init__(self, nc):
                self.nc = nc
                self.error = None
                self.error_cb_called = False
                self.close_cb_called = False
                self.disconnected_cb_called = False
                self.reconnected_cb_called = False
                self.msgs = []

            @tornado.gen.coroutine
            def subscription_handler(self, msg):
                yield self.nc.publish(msg.reply, 'hi')

            def error_cb(self, err):
                self.error = err
                self.error_cb_called = True

            def close_cb(self):
                self.close_cb_called = True

            def disconnected_cb(self):
                self.disconnected_cb_called = True

            def reconnected_cb(self):
                self.reconnected_cb_called = True

        nc = Client()
        c = Component(nc)
        options = {
            "servers": ["nats://127.0.0.1:4444"],
            "io_loop": self.io_loop,
            "close_cb": c.close_cb,
            "error_cb": c.error_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnected_cb": c.reconnected_cb
        }

        yield c.nc.connect(**options)
        yield c.nc.subscribe("hello", cb=c.subscription_handler)
        yield c.nc.flush()
        for i in range(0, 10):
            msg = yield c.nc.timed_request("hello", b'world')
            c.msgs.append(msg)
        self.assertEqual(len(c.msgs), 10)
        self.assertFalse(c.disconnected_cb_called)
        self.assertFalse(c.close_cb_called)
        self.assertFalse(c.error_cb_called)
        self.assertFalse(c.reconnected_cb_called)

        # Should be able to close normally
        yield c.nc.close()
        self.assertTrue(c.disconnected_cb_called)
        self.assertTrue(c.close_cb_called)
        self.assertFalse(c.error_cb_called)
        self.assertFalse(c.reconnected_cb_called)

    @tornado.testing.gen_test(timeout=15)
    def test_tls_reconnection(self):
        class Component:
            def __init__(self, nc):
                self.nc = nc
                self.error = None
                self.error_cb_called = False
                self.close_cb_called = False
                self.disconnected_cb_called = False
                self.reconnected_cb_called = False
                self.msgs = []
                self.reconnected_future = tornado.concurrent.Future()
                self.disconnected_future = tornado.concurrent.Future()

            @tornado.gen.coroutine
            def subscription_handler(self, msg):
                yield self.nc.publish(msg.reply, 'hi')

            def error_cb(self, err):
                self.error = err
                self.error_cb_called = True

            def close_cb(self):
                self.close_cb_called = True

            def disconnected_cb(self):
                self.disconnected_cb_called = True
                if not self.disconnected_future.done():
                    self.disconnected_future.set_result(True)

            def reconnected_cb(self):
                self.reconnected_cb_called = True
                if not self.reconnected_future.done():
                    self.reconnected_future.set_result(True)

        nc = Client()
        c = Component(nc)
        options = {
            "dont_randomize": True,
            "servers": [
                "nats://127.0.0.1:4444",
                "nats://127.0.0.1:4445",
            ],
            "loop": self.io_loop,
            "closed_cb": c.close_cb,
            "error_cb": c.error_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnected_cb": c.reconnected_cb,
            "reconnect_time_wait": 0.1,
            "max_reconnect_attempts": 5
        }

        yield c.nc.connect(**options)
        yield c.nc.subscribe("hello", cb=c.subscription_handler)
        yield c.nc.flush()
        for i in range(0, 5):
            msg = yield c.nc.request("hello", b'world')
            c.msgs.append(msg)
        self.assertEqual(5, len(c.msgs))

        # Trigger disconnect...
        orig_gnatsd = self.server_pool.pop(0)
        orig_gnatsd.finish()

        try:
            a = nc._current_server

            # Wait for reconnect logic kick in...
            yield tornado.gen.with_timeout(
                timedelta(seconds=5), c.disconnected_future)
            yield tornado.gen.with_timeout(
                timedelta(seconds=5), c.reconnected_future)
        finally:
            b = nc._current_server
            self.assertNotEqual(a.uri, b.uri)
        self.assertTrue(c.disconnected_cb_called)
        self.assertFalse(c.close_cb_called)
        self.assertFalse(c.error_cb_called)
        self.assertTrue(c.reconnected_cb_called)

        for i in range(0, 5):
            msg = yield c.nc.request("hello", b'world')
            c.msgs.append(msg)
        self.assertEqual(len(c.msgs), 10)

        # Should be able to close normally
        yield c.nc.close()
        self.assertTrue(c.disconnected_cb_called)
        self.assertTrue(c.close_cb_called)
        self.assertFalse(c.error_cb_called)
        self.assertTrue(c.reconnected_cb_called)


class ClientTLSCertsTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        super(ClientTLSCertsTest, self).setUp()

    class Component:
        def __init__(self, nc):
            self.nc = nc
            self.error = None
            self.error_cb_called = False
            self.close_cb_called = False
            self.disconnected_cb_called = False
            self.reconnected_cb_called = False
            self.msgs = []

        @tornado.gen.coroutine
        def subscription_handler(self, msg):
            yield self.nc.publish(msg.reply, 'hi')

        def error_cb(self, err):
            self.error = err
            self.error_cb_called = True

        def close_cb(self):
            self.close_cb_called = True

        def disconnected_cb(self):
            self.disconnected_cb_called = True

        def reconnected_cb(self):
            self.reconnected_cb_called = True

    @tornado.testing.gen_test(timeout=10)
    def test_tls_verify(self):
        nc = Client()
        c = self.Component(nc)
        options = {
            "servers": ["nats://127.0.0.1:4446"],
            "allow_reconnect": False,
            "io_loop": self.io_loop,
            "close_cb": c.close_cb,
            "error_cb": c.error_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnected_cb": c.reconnected_cb,
            "tls": {
                "cert_reqs": ssl.CERT_REQUIRED,
                "ca_certs": "./tests/configs/certs/ca.pem",
                "keyfile": "./tests/configs/certs/client-key.pem",
                "certfile": "./tests/configs/certs/client-cert.pem"
            }
        }

        conf = """
          port: 4446
          net: 127.0.0.1

          http_port: 8446
          tls {
            cert_file: './tests/configs/certs/server-cert.pem'
            key_file:  './tests/configs/certs/server-key.pem'
            ca_file:   './tests/configs/certs/ca.pem'
            timeout:   10
            verify:    true
          }
          """

        with Gnatsd(port=4446, http_port=8446, conf=conf) as gnatsd:
            yield c.nc.connect(**options)
            yield c.nc.subscribe("hello", cb=c.subscription_handler)
            yield c.nc.flush()
            for i in range(0, 10):
                msg = yield c.nc.timed_request("hello", b'world')
                c.msgs.append(msg)
            self.assertEqual(len(c.msgs), 10)
            self.assertFalse(c.disconnected_cb_called)
            self.assertFalse(c.close_cb_called)
            self.assertFalse(c.error_cb_called)
            self.assertFalse(c.reconnected_cb_called)

            # Should be able to close normally
            yield c.nc.close()
            self.assertTrue(c.disconnected_cb_called)
            self.assertTrue(c.close_cb_called)
            self.assertFalse(c.error_cb_called)
            self.assertFalse(c.reconnected_cb_called)

    @tornado.testing.gen_test(timeout=10)
    def test_tls_verify_short_timeout_no_servers_available(self):
        nc = Client()
        c = self.Component(nc)
        options = {
            "servers": ["nats://127.0.0.1:4446"],
            "allow_reconnect": False,
            "io_loop": self.io_loop,
            "close_cb": c.close_cb,
            "error_cb": c.error_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnected_cb": c.reconnected_cb,
            "tls": {
                "cert_reqs": ssl.CERT_REQUIRED,
                "ca_certs": "./tests/configs/certs/ca.pem",
                "keyfile": "./tests/configs/certs/client-key.pem",
                "certfile": "./tests/configs/certs/client-cert.pem"
            }
        }

        conf = """
          # port: 4446
          port: 4446
          net: 127.0.0.1

          http_port: 8446
          tls {
            cert_file: './tests/configs/certs/server-cert.pem'
            key_file:  './tests/configs/certs/server-key.pem'
            ca_file:   './tests/configs/certs/ca.pem'
            timeout:   0.0001
            verify:    true
          }
          """

        with Gnatsd(port=4446, http_port=8446, conf=conf) as gnatsd:
            with self.assertRaises(ErrNoServers):
                yield c.nc.connect(**options)

    @tornado.testing.gen_test(timeout=10)
    def test_tls_verify_fails(self):
        nc = Client()
        c = self.Component(nc)
        port = 4447
        http_port = 8447
        options = {
            "servers": ["nats://127.0.0.1:%d" % port],
            "max_reconnect_attempts": 5,
            "io_loop": self.io_loop,
            "close_cb": c.close_cb,
            "error_cb": c.error_cb,
            "disconnected_cb": c.disconnected_cb,
            "reconnected_cb": c.reconnected_cb,
            "reconnect_time_wait": 0.1,
            "tls": {
                "cert_reqs": ssl.CERT_REQUIRED,
                # "ca_certs": "./tests/configs/certs/ca.pem",
                "keyfile": "./tests/configs/certs/client-key.pem",
                "certfile": "./tests/configs/certs/client-cert.pem"
            }
        }

        conf = """
          port: %d
          net: 127.0.0.1

          http_port: %d
          tls {
            cert_file: './tests/configs/certs/server-cert.pem'
            key_file:  './tests/configs/certs/server-key.pem'
            ca_file:   './tests/configs/certs/ca.pem'
            timeout:   10
            verify: true
          }
          """ % (port, http_port)

        with Gnatsd(port=port, http_port=http_port, conf=conf) as gnatsd:
            with self.assertRaises(NatsError):
                yield c.nc.connect(**options)


class ShortControlLineNATSServer(tornado.tcpserver.TCPServer):
    @tornado.gen.coroutine
    def handle_stream(self, stream, address):
        while True:
            try:
                info_line = """INFO {"max_payload": 1048576, "tls_required": false, "server_id":"zrPhBhrjbbUdp2vndDIvE7"}\r\n"""
                yield stream.write(info_line)

                # Client will be awaiting for a pong next before reaching connected state.
                yield stream.write("""PONG\r\n""")
                yield tornado.gen.sleep(1)
            except tornado.iostream.StreamClosedError:
                break


class LargeControlLineNATSServer(tornado.tcpserver.TCPServer):
    @tornado.gen.coroutine
    def handle_stream(self, stream, address):
        while True:
            try:
                line = """INFO {"max_payload": 1048576, "tls_required": false, "server_id":"%s"}\r\n"""
                info_line = line % ("a" * 2048)
                yield stream.write(info_line)

                # Client will be awaiting for a pong next before reaching connected state.
                yield stream.write("""PONG\r\n""")
                yield tornado.gen.sleep(1)
            except tornado.iostream.StreamClosedError:
                break


class ClientConnectTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        super(ClientConnectTest, self).setUp()

    def tearDown(self):
        super(ClientConnectTest, self).tearDown()

    @tornado.testing.gen_test(timeout=5)
    def test_connect_info_large_protocol_line(self):
        # Start mock TCP Server
        server = LargeControlLineNATSServer()
        server.listen(4229)
        nc = Client()
        options = {
            "dont_randomize": True,
            "servers": ["nats://127.0.0.1:4229"],
            "io_loop": self.io_loop,
            "verbose": False
        }
        yield nc.connect(**options)
        self.assertTrue(nc.is_connected)

    @tornado.testing.gen_test(timeout=5)
    def test_connect_info_large_protocol_line_2(self):
        # Start mock TCP Server
        server = ShortControlLineNATSServer()
        server.listen(4229)
        nc = Client()
        options = {
            "dont_randomize": True,
            "servers": ["nats://127.0.0.1:4229"],
            "io_loop": self.io_loop,
            "verbose": False
        }
        yield nc.connect(**options)
        self.assertTrue(nc.is_connected)


class ClientClusteringDiscoveryTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        super(ClientClusteringDiscoveryTest, self).setUp()

    def tearDown(self):
        super(ClientClusteringDiscoveryTest, self).tearDown()

    @tornado.testing.gen_test(timeout=15)
    def test_servers_discovery(self):
        conf = """
        cluster {
          routes = [
            nats-route://127.0.0.1:6222
          ]
        }
        """

        nc = Client()
        options = {
            "servers": ["nats://127.0.0.1:4222"],
            "io_loop": self.io_loop,
        }

        with Gnatsd(port=4222, http_port=8222, cluster_port=6222, conf=conf) as nats1:
            yield nc.connect(**options)
            yield tornado.gen.sleep(1)
            initial_uri = nc.connected_url
            with Gnatsd(port=4223, http_port=8223, cluster_port=6223, conf=conf) as nats2:
                yield tornado.gen.sleep(1)
                srvs = {}
                for item in nc._server_pool:
                    srvs[item.uri.port] = True
                self.assertEqual(len(srvs.keys()), 2)

                with Gnatsd(port=4224, http_port=8224, cluster_port=6224, conf=conf) as nats3:
                    yield tornado.gen.sleep(1)
                    for item in nc._server_pool:
                        srvs[item.uri.port] = True
                    self.assertEqual(3, len(srvs.keys()))

                    srvs = {}
                    for item in nc.discovered_servers:
                        srvs[item.uri.port] = True
                    self.assertTrue(2 <= len(srvs.keys()) <= 3)

                    srvs = {}
                    for item in nc.servers:
                        srvs[item.uri.port] = True
                    self.assertEqual(3, len(srvs.keys()))

                    # Terminate the first server and wait for reconnect
                    nats1.finish()
                    yield tornado.gen.sleep(1)
                    final_uri = nc.connected_url
                    self.assertNotEqual(initial_uri, final_uri)
        yield nc.close()

    @tornado.testing.gen_test(timeout=15)
    def test_servers_discovery_no_randomize(self):
        conf = """
        cluster {
          routes = [
            nats-route://127.0.0.1:6232
          ]
        }
        """

        nc = Client()
        options = {
            "servers": ["nats://127.0.0.1:4232"],
            "dont_randomize": True,
            "loop": self.io_loop,
        }

        with Gnatsd(
                port=4232, http_port=8232, cluster_port=6232,
                conf=conf) as nats1:
            yield nc.connect(**options)
            yield tornado.gen.sleep(1)
            with Gnatsd(
                    port=4233, http_port=8233, cluster_port=6233,
                    conf=conf) as nats2:
                yield tornado.gen.sleep(1)
                srvs = []
                for item in nc._server_pool:
                    if item.uri.port not in srvs:
                        srvs.append(item.uri.port)
                self.assertEqual(len(srvs), 2)

                with Gnatsd(
                        port=4234, http_port=8234, cluster_port=6234,
                        conf=conf) as nats3:
                    yield tornado.gen.sleep(1)
                    for item in nc._server_pool:
                        if item.uri.port not in srvs:
                            srvs.append(item.uri.port)
                    self.assertEqual([4232, 4233, 4234], srvs)
        yield nc.close()

    @tornado.testing.gen_test(timeout=15)
    def test_servers_discovery_auth_reconnect(self):
        conf = """
        cluster {
          routes = [
            nats-route://127.0.0.1:6222
          ]
        }

        authorization {
          user = foo
          pass = bar
        }
        """

        reconnected_future = tornado.concurrent.Future()

        @tornado.gen.coroutine
        def reconnected_cb():
            reconnected_future.set_result(True)

        nc = Client()
        options = {
            "servers": ["nats://127.0.0.1:4222"],
            "loop": self.io_loop,
            "user": "foo",
            "password": "bar",
            "reconnected_cb": reconnected_cb,
        }

        with Gnatsd(port=4222, http_port=8222, cluster_port=6222, conf=conf) as nats1:
            yield nc.connect(**options)
            yield tornado.gen.sleep(1)
            initial_uri = nc.connected_url
            with Gnatsd(port=4223, http_port=8223, cluster_port=6223, conf=conf) as nats2:
                yield tornado.gen.sleep(1)
                srvs = {}
                for item in nc._server_pool:
                    srvs[item.uri.port] = True
                self.assertEqual(len(srvs.keys()), 2)

                with Gnatsd(port=4224, http_port=8224, cluster_port=6224, conf=conf) as nats3:
                    yield tornado.gen.sleep(1)
                    for item in nc._server_pool:
                        srvs[item.uri.port] = True
                    self.assertEqual(3, len(srvs.keys()))

                    srvs = {}
                    for item in nc.discovered_servers:
                        srvs[item.uri.port] = True
                    self.assertTrue(2 <= len(srvs.keys()) <= 3)

                    srvs = {}
                    for item in nc.servers:
                        srvs[item.uri.port] = True
                    self.assertEqual(3, len(srvs.keys()))

                    # Terminate the first server and wait for reconnect
                    nats1.finish()

                    yield tornado.gen.with_timeout(
                        timedelta(seconds=1), reconnected_future)

                    # Check if the connection is ok
                    received = tornado.concurrent.Future()

                    @tornado.gen.coroutine
                    def handler(msg):
                        received.set_result(msg)

                    yield nc.subscribe("foo", cb=handler)
                    yield nc.flush()
                    yield nc.publish("foo", b'bar')

                    yield tornado.gen.with_timeout(
                        timedelta(seconds=1), received)

                    final_uri = nc.connected_url
                    self.assertNotEqual(initial_uri, final_uri)
        yield nc.close()

class ClientDrainTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        print("\n=== RUN {0}.{1}".format(self.__class__.__name__,
                                         self._testMethodName))
        self.threads = []
        self.server_pool = []

        server = Gnatsd(port=4225, http_port=8225)
        self.server_pool.append(server)

        for gnatsd in self.server_pool:
            t = threading.Thread(target=gnatsd.start)
            self.threads.append(t)
            t.start()

        http = tornado.httpclient.HTTPClient()
        while True:
            try:
                response = http.fetch('http://127.0.0.1:8225/varz')
                if response.code == 200:
                    break
                continue
            except:
                time.sleep(0.1)
                continue
        super(ClientDrainTest, self).setUp()

    def tearDown(self):
        for gnatsd in self.server_pool:
            gnatsd.finish()
        for t in self.threads:
            t.join()
        super(ClientDrainTest, self).tearDown()

    @tornado.testing.gen_test
    def test_drain_closes_connection(self):
        nc = Client()
        future = tornado.concurrent.Future()

        @tornado.gen.coroutine
        def closed_cb():
            future.set_result(True)

        @tornado.gen.coroutine
        def cb(msg):
            pass

        yield nc.connect("127.0.0.1:4225",
                         loop=self.io_loop,
                         closed_cb=closed_cb,
                         )
        yield nc.subscribe("foo", cb=cb)
        yield nc.subscribe("bar", cb=cb)
        yield nc.subscribe("quux", cb=cb)
        yield nc.drain()
        yield tornado.gen.with_timeout(timedelta(seconds=1), future)
        self.assertEqual(0, len(nc._subs))
        self.assertTrue(True, nc.is_closed)

    @tornado.testing.gen_test
    def test_drain_invalid_subscription(self):
        nc = NATS()

        yield nc.connect("127.0.0.1:4225",
                         loop=self.io_loop,
                         )
        msgs = []

        @tornado.gen.coroutine
        def cb(msg):
            msgs.append(msg)

        yield nc.subscribe("foo", cb=cb)
        yield nc.subscribe("bar", cb=cb)
        yield nc.subscribe("quux", cb=cb)

        with self.assertRaises(ErrBadSubscription):
            yield nc.drain(sid=4)
        yield nc.close()
        self.assertTrue(nc.is_closed)

    @tornado.testing.gen_test
    def test_drain_single_subscription(self):
        nc = NATS()
        yield nc.connect("127.0.0.1:4225", loop=self.io_loop)

        msgs = []

        @tornado.gen.coroutine
        def handler(msg):
            msgs.append(msg)
            if len(msgs) == 10:
                yield tornado.gen.sleep(0.5)

        sid = yield nc.subscribe("foo", cb=handler)

        for i in range(0, 200):
            yield nc.publish("foo", b'hi')

            # Relinquish control so that messages are processed.
            yield tornado.gen.sleep(0)
        yield nc.flush()

        sub = nc._subs[sid]
        before_drain = sub.pending_queue.qsize()
        self.assertTrue(before_drain > 0)

        drain_task = yield nc.drain(sid=sid)
        yield tornado.gen.with_timeout(timedelta(seconds=1), drain_task)

        for i in range(0, 200):
            yield nc.publish("foo", b'hi')

            # Relinquish control so that messages are processed.
            yield tornado.gen.sleep(0)

        # No more messages should have been processed.
        after_drain = sub.pending_queue.qsize()
        self.assertEqual(0, after_drain)
        self.assertEqual(200, len(msgs))

        yield nc.close()
        self.assertTrue(nc.is_closed)
        self.assertFalse(nc.is_connected)

    @tornado.testing.gen_test(timeout=15)
    def test_drain_connection(self):
        nc = NATS()
        errors = []
        drain_done = tornado.concurrent.Future()

        def disconnected_cb():
            pass

        def reconnected_cb():
            pass

        def error_cb(e):
            errors.append(e)

        def closed_cb():
            drain_done.set_result(True)

        yield nc.connect("127.0.0.1:4225",
                         loop=self.io_loop,
                         closed_cb=closed_cb,
                         error_cb=error_cb,
                         reconnected_cb=reconnected_cb,
                         disconnected_cb=disconnected_cb,
                         )

        nc2 = NATS()
        yield nc2.connect("127.0.0.1:4225", loop=self.io_loop)

        msgs = []

        @tornado.gen.coroutine
        def foo_handler(msg):
            if len(msgs) % 20 == 1:
                yield tornado.gen.sleep(0.2)
            if len(msgs) % 50 == 1:
                yield tornado.gen.sleep(0.5)
            if msg.reply != "":
                yield nc.publish_request(msg.reply, "foo", b'OK!')
            yield nc.flush()

        @tornado.gen.coroutine
        def bar_handler(msg):
            if len(msgs) % 20 == 1:
                yield tornado.gen.sleep(0.2)
            if len(msgs) % 50 == 1:
                yield tornado.gen.sleep(0.5)
            if msg.reply != "":
                yield nc.publish_request(msg.reply, "bar", b'OK!')
            yield nc.flush()

        @tornado.gen.coroutine
        def quux_handler(msg):
            if len(msgs) % 20 == 1:
                yield tornado.gen.sleep(0.2)
            if len(msgs) % 50 == 1:
                yield tornado.gen.sleep(0.5)
            if msg.reply != "":
                yield nc.publish_request(msg.reply, "quux", b'OK!')
            yield nc.flush()

        sid_foo = yield nc.subscribe("foo", cb=foo_handler)
        sid_bar = yield nc.subscribe("bar", cb=bar_handler)
        sid_quux = yield nc.subscribe("quux", cb=quux_handler)

        @tornado.gen.coroutine
        def replies(msg):
            msgs.append(msg)

        yield nc2.subscribe("my-replies.*", cb=replies)
        for i in range(0, 201):
            yield nc2.publish_request("foo", "my-replies.AAA", b'help')
            yield nc2.publish_request("bar", "my-replies.BBB", b'help')
            yield nc2.publish_request("quux", "my-replies.CCC", b'help')

            # Relinquish control so that messages are processed.
            yield tornado.gen.sleep(0)
        yield nc2.flush()

        sub_foo = nc._subs[sid_foo]
        sub_bar = nc._subs[sid_bar]
        sub_quux = nc._subs[sid_quux]
        self.assertTrue(sub_foo.pending_queue.qsize() > 0)
        self.assertTrue(sub_bar.pending_queue.qsize() > 0)
        self.assertTrue(sub_quux.pending_queue.qsize() > 0)

        # Drain and close the connection. In case of timeout then
        # an async error will be emitted via the error callback.
        self.io_loop.spawn_callback(nc.drain)

        # Let the draining task a bit of time to run...
        yield tornado.gen.sleep(0.5)

        # Should be no-op or bail if connection closed.
        yield nc.drain()

        # State should be closed here already,
        yield tornado.gen.with_timeout(timedelta(seconds=10), drain_done)
        self.assertEqual(0, len(nc._subs.items()))
        self.assertEqual(1, len(nc2._subs.items()))
        self.assertTrue(len(msgs) > 150)

        # No need to close first connection since drain reaches
        # the closed state.
        yield nc2.close()
        self.assertTrue(nc.is_closed)
        self.assertFalse(nc.is_connected)
        self.assertTrue(nc2.is_closed)
        self.assertFalse(nc2.is_connected)

    @tornado.testing.gen_test(timeout=15)
    def test_drain_connection_timeout(self):
        nc = NATS()
        errors = []
        drain_done = tornado.concurrent.Future()

        @tornado.gen.coroutine
        def error_cb(e):
            errors.append(e)

        @tornado.gen.coroutine
        def closed_cb():
            drain_done.set_result(True)

        yield nc.connect("127.0.0.1:4225",
                         loop=self.io_loop,
                         closed_cb=closed_cb,
                         error_cb=error_cb,
                         drain_timeout=0.1,
                         )

        nc2 = NATS()
        yield nc2.connect("127.0.0.1:4225", loop=self.io_loop)

        msgs = []

        @tornado.gen.coroutine
        def handler(msg):
            if len(msgs) % 20 == 1:
                yield tornado.gen.sleep(0.2)
            if len(msgs) % 50 == 1:
                yield tornado.gen.sleep(0.5)
            if msg.reply != "":
                yield nc.publish_request(msg.reply, "foo", b'OK!')
            yield nc.flush()
        sid_foo = yield nc.subscribe("foo", cb=handler)

        @tornado.gen.coroutine
        def replies(msg):
            msgs.append(msg)

        yield nc2.subscribe("my-replies.*", cb=replies)
        for i in range(0, 201):
            yield nc2.publish_request("foo", "my-replies.AAA", b'help')
            yield nc2.publish_request("bar", "my-replies.BBB", b'help')
            yield nc2.publish_request("quux", "my-replies.CCC", b'help')

            # Relinquish control so that messages are processed.
            yield tornado.gen.sleep(0)
        yield nc2.flush()

        # Drain and close the connection. In case of timeout then
        # an async error will be emitted via the error callback.
        yield nc.drain()
        self.assertTrue(type(errors[0]) is ErrDrainTimeout)

        # No need to close first connection since drain reaches
        # the closed state.
        yield nc2.close()
        self.assertTrue(nc.is_closed)
        self.assertFalse(nc.is_connected)
        self.assertTrue(nc2.is_closed)
        self.assertFalse(nc2.is_connected)

if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(verbosity=2, exit=False, testRunner=runner)
