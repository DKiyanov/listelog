from ldap3 import Server, Connection, ALL, SIMPLE
from src.config import Config

def user_authentication(config: Config, login: str, password: str) -> bool:
    """Выполняет аутентификацию пользователя в Active Directory."""

    if login == config.demo_login and password == config.demo_login:
        return True

    user_principal: str =  f"{login}@{config.ldap_domain}"
    
    try:
        server = Server(config.ldap_server, get_info=ALL, use_ssl=False) # Выставите use_ssl=True для продуктивного LDAPS
        # Попытка установить соединение (Bind) с учетными данными пользователя
        with Connection(server, user=user_principal, password=password, authentication=SIMPLE, raise_exceptions=True) as conn:
            return conn.bound
    except Exception as e:
        # В проде рекомендуется логировать ошибку (без пароля!), а не просто гасить
        print(f"user_authentication error: {e}")
        return False
