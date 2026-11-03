import keyring
import keyring.errors

from src.net.net_client import *
from src.translate import *

class NetClientLogin:
    def __init__(self, net_client: NetClient) -> None:
        self.net_client = net_client
        self.last_error: str = ""

    def login(self, base_url: str, username: str, token:str) -> bool:
        return self.login_ex(base_url, username, token)

    def login_ex(self, base_url: str, username: str, token: Optional[str]) -> bool:
        """Выполняет автоматический вход по сохраненным данным."""
        self.last_error = ""

        try:
            # Чтение токена из keyring
            if token is None or token == "":
                token = keyring.get_password(SERVICE_NAME, username)

                if not token:
                    self.last_error = f"{lcvt('0801|Токен для пользователя')} '{username}' {lcvt('0802|не найден в локальном хранилище.')}"
                    return False

            self.net_client.check_token(base_url, username, token)

            return True

        except keyring.errors.KeyringError as e:
            self.last_error = f"{lcvt('0803|Ошибка чтения из системного хранилища (keyring):')} {str(e)}"
            return False
        except httpx.HTTPStatusError as e:
            self.last_error = f"{lcvt('0804|Ошибка проверки токена')} ({e.response.status_code}): {e.response.text}"
            return False
        except httpx.RequestError as e:
            self.last_error = f"{lcvt('0805|Ошибка сети при проверке токена:')} {str(e)}"
            return False

    def logup(self, base_url: str, username: str, password: str) -> bool:
        """Выполняет первичную авторизацию и сохраняет ключ в secure-store."""
        self.last_error = ""

        try:
            token = self.net_client.get_token(base_url, username, password)

            # Сохранение токена в системное хранилище
            keyring.set_password(SERVICE_NAME, username, token)

            return True

        except httpx.HTTPStatusError as e:
            self.last_error = f"{lcvt('0806|Ошибка сервера')} ({e.response.status_code}): {e.response.text}"
            return False
        except httpx.RequestError as e:
            self.last_error = f"{lcvt('0807|Ошибка сети при запросе:')} {str(e)}"
            return False
        except ValueError:
            self.last_error = lcvt("0808|Сервер вернул некорректный JSON.")
            return False
        except keyring.errors.KeyringError as e:
            self.last_error = f"{lcvt('0809|Ошибка системного хранилища (keyring):')} {str(e)}"
            return False
