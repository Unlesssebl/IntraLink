import pytest
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_socket_getfqdn():
    # Мокаем socket.getfqdn глобально для всех тестов,
    # чтобы предотвратить зависания на DNS-запросах для несуществующих хостов (srv, target-pc и т.д.)
    with patch("socket.getfqdn", side_effect=lambda host: host) as mock:
        yield mock
