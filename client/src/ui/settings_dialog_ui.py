import flet as ft

from src.net.net_client import *
from src.config_manager import *
from src.utils import *
from src.translate import *

def set_theme_from_str(page: ft.Page, theme_name: str | None) -> None:
    match theme_name:
        case "light":
            page.theme_mode = ft.ThemeMode.LIGHT
        case "dark":
            page.theme_mode = ft.ThemeMode.DARK
        case "system":
            page.theme_mode = ft.ThemeMode.SYSTEM   

class SettingsDialog:
    """Класс диалогового окна настроек."""
    
    def __init__(self, page: ft.Page, config_manager: ConfigManager, net_client: NetClient, on_success_auth: Callable[[], None],) -> None:
        self.page: ft.Page = page
        self.config_manager: ConfigManager = config_manager
        self.net_client: NetClient = net_client
        self.on_success_auth: Callable[[], None] = on_success_auth        

        # Инициализация элементов управления
        self.url_input: ft.TextField = ft.TextField(
            label=lcvt("0401|Адрес сервера"),
            hint_text="https://example.com",
            keyboard_type=ft.KeyboardType.URL,
        )
        
        self.login_input: ft.TextField = ft.TextField(
            label=lcvt("0402|Имя пользователя"),
        )
        
        self.password_input: ft.TextField = ft.TextField(
            label=lcvt("0403|Пароль"),
            password=True,
            can_reveal_password=True,
        )

        # Цветовая тема
        def theme_changed(e):
            set_theme_from_str(page, self.theme_dropdown.value)
            page.update()
        
        self.theme_dropdown = ft.Dropdown(
            label=lcvt("0404|Тема оформления"),
            value="system",  # Элемент по умолчанию
            options=[
                ft.dropdown.Option(key="system", text=lcvt("0405|Системная")),
                ft.dropdown.Option(key="light", text=lcvt("0406|Светлая")),
                ft.dropdown.Option(key="dark", text=lcvt("0407|Тёмная")),
            ],
            on_select=theme_changed,
            expand=True
        )

        theme_row = ft.Row(
            controls=[self.theme_dropdown]
        )

        # Выбор языка
        self.system_language: str = get_os_language()
        
        def language_changed(e):
            LanguageManager().load_language(self.language_dropdown.value)
            page.update()

        languages = LanguageManager().get_available_languages()
        lang_options: list[ft.dropdown.Option] = [
            ft.dropdown.Option(key=key, text=value) 
            for key, value in languages.items()
        ]        
        
        self.language_dropdown = ft.Dropdown(
            label=lcvt("0417|Язык интерфейса"),
            value=self.system_language,  # Элемент по умолчанию
            options=lang_options,
            on_select=language_changed,
            expand=True
        )

        language_row = ft.Row(
            controls=[self.language_dropdown]
        )

        self.dialog: ft.AlertDialog = ft.AlertDialog(
            title=ft.Text(lcvt("0408|Настройки")),
            content=ft.Column(
                controls=[
                    self.url_input,
                    self.login_input,
                    self.password_input,
                    theme_row,
                    language_row
                ],
                tight=True,
                spacing=15,
            ),
            actions=[
                ft.Button(lcvt("0409|Отмена"), icon=ft.Icons.CHECK , on_click=self._handle_cancel),
                ft.Button(lcvt("0410|Ок"), icon=ft.Icons.CANCEL, on_click=self._handle_ok),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            modal=True,
        )     

    def _dialog_open(self, dialog: ft.AlertDialog):
        self.page.show_dialog(dialog)
        
    def _dialog_close(self, dialog: ft.AlertDialog):
        dialog.open =False
        self.page.update()
        

    def open(self) -> None:
        """Открывает диалоговое окно и заполняет поля актуальными данными."""

        self.url_input.value = self.config_manager.base_url
        self.url_input.read_only = self.config_manager.base_url_lock

        self.login_input.value = self.config_manager.login
        self.login_input.read_only = self.config_manager.login_lock  

        if self.config_manager.login != "":
            self.login_input.value = self.config_manager.login
        else:
            self.login_input.value = self.config_manager.get_system_username()
            
        self.password_input.value = ""
        self.password_input.read_only = self.config_manager.password_lock
        if self.config_manager.password_lock:
            self.password_input.hint_text = lcvt("0411|Введён токен")

        self.url_input.error = None
        self.login_input.error = None
        self.password_input.error = None

        self.theme_dropdown.value = self.config_manager.theme or "system"
        self.language_dropdown.value = self.config_manager.language or self.system_language

        self._dialog_open(self.dialog)      


    def _validate(self) -> bool:
        """Проверяет обязательность заполнения полей ввода."""
        is_valid: bool = True
        
        if not self.url_input.value or not self.url_input.value.strip():
            self.url_input.error = lcvt("0412|Поле обязательно для заполнения")
            is_valid = False
        else:
            self.url_input.error = None

        if not self.login_input.value or not self.login_input.value.strip():
            self.login_input.error = lcvt("0413|Поле обязательно для заполнения")
            is_valid = False
        else:
            self.login_input.error = None

        if ((not self.password_input.value or not self.password_input.value.strip()) 
            and not self.config_manager.password_lock):
            self.password_input.error = lcvt("0414|Поле обязательно для заполнения")
            is_valid = False
        else:
            self.password_input.error = None

        self.page.update()
        return is_valid

    def _handle_ok(self) -> None:
        """Обработчик нажатия кнопки 'Ок'."""
        if not self._validate():
            return

        base_url: str = self.url_input.value.strip()
        login_username: str = self.login_input.value.strip()
        password: str = self.password_input.value
        theme = self.theme_dropdown.value or "system"
        language = self.language_dropdown.value or self.system_language

        if self.config_manager.password_lock:
            success = self.net_client.login(self.config_manager)
            if not success:
                if " (401): {" in self.net_client.last_error:
                    show_toast(self.page, lcvt("0415|Необходимо обновить токен"))
                else:
                    show_toast(self.page, self.net_client.last_error)
        else:
            success = self.net_client.logup(base_url, login_username, password)
        
        if success:
            self.config_manager.save(base_url, login_username, theme, language)            
            self._dialog_close(self.dialog) 
            self.on_success_auth()
        else:
            self.password_input.error = lcvt("0416|Ошибка авторизации на сервере")
            self.page.update()

    def _handle_cancel(self) -> None:
        """Обработчик нажатия кнопки 'Отмена'."""
        self._dialog_close(self.dialog) 
