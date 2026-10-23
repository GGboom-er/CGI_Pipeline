"""RPyC logging and transport lifecycle checks without importing Maya."""
import errno
import logging
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import rpyc
from rpyc.utils.authenticators import AuthenticationError

from cgi_pipeline.hosts.maya import rpyc_server as bridge


def _server(sock, error=None, authenticator=None):
    server = object.__new__(bridge._SilentThreadedServer)
    server.logger = Mock()
    server.clients = {sock}
    server.authenticator = authenticator
    server._serve_client = Mock(side_effect=error)
    return server


def test_client_socket_is_closed_and_removed():
    sock = Mock()
    server = _server(sock)
    server._authenticate_and_serve_client(sock)
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    sock.close.assert_called_once_with()
    assert not server.clients


def test_already_disconnected_socket_is_closed_and_close_errors_are_reported():
    sock = Mock()
    sock.shutdown.side_effect = OSError(errno.ENOTCONN, 'not connected')
    sock.close.side_effect = OSError(errno.EBADF, 'invalid socket')
    server = _server(sock)
    server._authenticate_and_serve_client(sock)
    sock.close.assert_called_once_with()
    server.logger.exception.assert_called_once_with('failed to close client socket')
    assert not server.clients


@pytest.mark.parametrize('error', [EOFError(), ConnectionResetError(),
                                  OSError(errno.ENOTCONN, 'not connected')])
def test_normal_disconnect_is_quiet_and_still_closes(error):
    sock = Mock()
    server = _server(sock, error)
    server._authenticate_and_serve_client(sock)
    server.logger.exception.assert_not_called()
    sock.close.assert_called_once_with()


@pytest.mark.parametrize('error', [ValueError('business bug'),
                                  OSError(errno.ENOENT, 'missing business file')])
def test_nontransport_failure_remains_visible(error):
    sock = Mock()
    server = _server(sock, error)
    server._authenticate_and_serve_client(sock)
    server.logger.exception.assert_called_once()
    sock.close.assert_called_once_with()


def test_authentication_rejection_warns_and_unexpected_failure_errors():
    for error, expected in [(AuthenticationError('rejected'), 'warning'),
                            (ValueError('auth bug'), 'exception')]:
        sock = Mock()
        server = _server(sock, authenticator=Mock(side_effect=error))
        server._authenticate_and_serve_client(sock)
        getattr(server.logger, expected).assert_called_once()
        server._serve_client.assert_not_called()
        sock.close.assert_called_once_with()


def test_authenticated_wrapper_and_original_socket_are_closed():
    sock, wrapper = Mock(), Mock()
    server = _server(sock, authenticator=lambda _: (wrapper, 'credentials'))
    server._authenticate_and_serve_client(sock)
    sock.close.assert_called_once_with()
    wrapper.close.assert_called_once_with()


def test_existing_server_refreshes_only_dedicated_logger(monkeypatch):
    root = logging.getLogger()
    root_state = (root.level, list(root.handlers))
    server = SimpleNamespace(logger=root, protocol_config={'logger': root},
                             registrar=SimpleNamespace(logger=root))
    monkeypatch.setattr(bridge.sys, '_cgi_rpyc_servers', {18812: server}, raising=False)
    create = Mock()
    monkeypatch.setattr(bridge, '_SilentThreadedServer', create)
    bridge._start_rpyc_server(18812)
    create.assert_not_called()
    assert server.logger.name == 'cgi_pipeline.maya.rpyc.18812'
    assert server.logger.level == logging.WARNING
    assert server.protocol_config['logger'] is server.logger
    assert server.registrar.logger is server.logger
    assert (root.level, list(root.handlers)) == root_state


def test_real_local_connections_are_quiet_but_warnings_survive(caplog):
    caplog.set_level(logging.INFO)
    ready, completed = threading.Event(), threading.Event()

    class Service(rpyc.Service):
        def exposed_ping(self):
            return 'ok'

        def exposed_fail(self):
            raise FileNotFoundError('business file missing')

    class Server(bridge._SilentThreadedServer):
        def _listen(self):
            super()._listen()
            ready.set()

        def _authenticate_and_serve_client(self, sock):
            try:
                super()._authenticate_and_serve_client(sock)
            finally:
                completed.set()

    logger = bridge._server_logger(0)
    server = Server(Service, hostname='127.0.0.1', port=0, logger=logger)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    try:
        assert ready.wait(5)
        for _ in range(3):
            completed.clear()
            with rpyc.connect('127.0.0.1', server.port) as conn:
                assert conn.root.ping() == 'ok'
                with pytest.raises(FileNotFoundError, match='business file missing'):
                    conn.root.fail()
            assert completed.wait(5)
            assert not server.clients
        logger.warning('visible warning')
        messages = [record.getMessage() for record in caplog.records
                    if record.name == logger.name]
        assert messages == ['visible warning']
    finally:
        server.close()
        thread.join(5)
        assert not thread.is_alive()
