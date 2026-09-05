from __future__ import annotations
import asyncio
from typing import Optional, List
import subprocess

import flet as ft

from src.models import *
from src.config_manager import *
from src.audio.my_audio import *
from src.file.file_saver import *
from src.net.net_client import *
from src.ui.settings_dialog_ui import *
from src.file.file_reader import *

from src.ui.file_upload_ui import *
from src.ui.device_row_ui import *
from src.ui.result_ui import *

from src.utils import *
from src.translate import lcvt

import os
import platform

current_os = platform.system()


if current_os == "Windows":
    os.environ["FLET_VIEW_PATH"] = get_work_path("flet_client/windows")
if current_os == "Linux":
    os.environ["FLET_VIEW_PATH"] = get_work_path("flet_client/linux")

class AudioRecorderApp:
    """Основной класс приложения, связывающий GUI и бизнес-логику."""

    def __init__(self, page: ft.Page) -> None:
        self.page: ft.Page = page

        # Скрываем стандартный заголовок и кнопки ОС  
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.padding = 0
        self.page.spacing = 0

        self.page.title = "Audio Capture"
        # self.page.window.width = 700
        # self.page.window.height = 250
        self.recording_on = False
        self.recording_source_list: list[RecordSourceInfo] = []
        self.recording_source_ids = set()

        # Бизнес-компоненты
        self.config_manager: ConfigManager = ConfigManager()
        self.encoder: Encoder = Encoder(output_format="ogg")
        self.proc_queue: ProcQueue = ProcQueue(encoder=self.encoder, on_packet_ready=self._on_encoded_chunk)
        self.file_saver = FileSaver()
        self.net_client = NetClient(self.on_send, self.on_results)

        set_theme_from_str(self.page, self.config_manager.theme)

        if self.config_manager.language:
            LanguageManager().load_language(self.config_manager.language)

        self.audio_packet_num: int = 0  # глобальный счётчик отправляемых на сервер пакетов

        self.result_view: ResultView = ResultView(self.page, self.net_client)

        self.settings_dialog: Optional[SettingsDialog] = None

        self.dev_rows: list[DeviceRow] = []
        self.dev_rows.append(
            DeviceRow(
                page, 
                DeviceType.MICROPHONE, 
                1, 
                self._on_raw_chank, 
                self._on_voice_chunk, 
                self._on_row_change, 
                self.on_mic_add_click,
                self.dev_rows
            )        
        )
        self.dev_rows.append(
            DeviceRow(
                page, 
                DeviceType.LOOPBACK, 
                100, 
                self._on_raw_chank, 
                self._on_voice_chunk, 
                self._on_row_change, 
                None,
                self.dev_rows
            )            
        )

        self.file_upload_row = FileUploadRow(self.page, self._on_file_upload_start, self._on_voice_chunk, self._on_file_finish)

        # GUI элементы
        self.main_status: ft.Text = ft.Text("", color=ft.Colors.BLUE, text_align=ft.TextAlign.CENTER)
        self.main_status_widget: ft.Row = ft.Row(
            controls=[
                ft.Container(
                    content=self.main_status,
                    bgcolor=ft.Colors.YELLOW,
                    padding=10,
                    expand=True,
                )
            ],
            visible=False
        )

        self.title: ft.TextField = ft.TextField(expand=True)
        self.btn_start: ft.Button = ft.Button(lcvt("0001|Старт"), icon=ft.Icons.PLAY_ARROW, on_click=self.start_recording,
                                              disabled=True)
        self.btn_stop: ft.Button = ft.Button(lcvt("0002|Стоп"), icon=ft.Icons.STOP, on_click=self.stop_recording, disabled=True)
        self.lbl_status: ft.Text = ft.Text(lcvt("0003|Статус: Ожидание"), color=ft.Colors.BLUE, expand=True)
        self.lbl_lag: ft.Text = ft.Text(lcvt("0004|Отставание: 0 сек."), color=ft.Colors.BLUE, visible=False)

        self.chk_srver_stt: ft.Checkbox = ft.Checkbox(label=lcvt("0005|Речь в текст"), value=True, label_position=ft.LabelPosition.LEFT)

        self.chk_record_to_file: ft.Checkbox = ft.Checkbox(label=lcvt("0006|Запись в файл"), value=False, label_position=ft.LabelPosition.LEFT)
        self.btn_open_record_folder: ft.IconButton = ft.IconButton(icon=ft.Icons.FOLDER_ROUNDED, on_click=self.open_record_folder, icon_color=ft.Colors.YELLOW, tooltip=lcvt("0007|Открыть папку с записанными файлами"))

        # self.btn_test: ft.Button = ft.Button("Тест", icon=ft.Icons.TELEGRAM, on_click=self.test_btn_click)

    def build_titlebar(self) -> ft.Container:
        custom_title_bar = ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            height=40,
            content=ft.Row(
                controls=[
                    ft.Container(width=4),
                    ft.Image(src="icon.png", width=24, height=24, fit=ft.BoxFit.FILL),                    

                    # WindowDragArea позволяет перетаскивать окно мышкой
                    ft.WindowDragArea(
                        content=ft.Container(
                            content=ft.Text(str(self.page.title), weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.only(left=10),
                        ),
                        expand=True,  # Растягивается на всю доступную ширину
                    ),

                    ft.IconButton(
                        icon=ft.Icons.WEB,
                        icon_color=ft.Colors.BLUE,
                        tooltip=lcvt("0008|Сессии сохранённые на сервере"),
                        on_click=self.open_self_site
                    ),

                    ft.IconButton(
                        icon=ft.Icons.SETTINGS,
                        icon_color=ft.Colors.BLUE,
                        tooltip=lcvt("0009|Настройки приложения"),
                        on_click=self.show_settings_dialog
                    ),

                    # Стандартные кнопки управления окном
                    ft.IconButton(
                        icon=ft.Icons.REMOVE,
                        tooltip=lcvt("0010|Свернуть"),
                        on_click=self.minimize_window
                    ),

                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        tooltip=lcvt("0011|Закрыть"),
                        icon_color=ft.Colors.RED_ACCENT,
                        on_click=self.close_window
                    ),
                ],
                spacing=0,
            )
        )
        return custom_title_bar

    def init_ui(self) -> None:
        """Сборка интерфейса."""

        custom_title_bar = self.build_titlebar()
        self.page.add(
            custom_title_bar,
        )

        self.page.add(
            self.main_status_widget
        )

        # Устройства поставщики звука
        for dev_row in self.dev_rows:
            row = dev_row.get_row_ui()
            self.page.add(
                ft.Container(content=row, padding=10)
            )

        file_upload_ui = self.file_upload_row.get_ui()
        self.page.add(
            ft.Container(content=file_upload_ui, padding=10)
        )

            # Управлением записью: Описание
        row_title: ft.Row = ft.Row(
            controls=[
                ft.Text(lcvt("0012|Описание:"), weight=ft.FontWeight.BOLD, width=135),
                self.title,
                ft.Container(width=40)
            ],
            alignment=ft.MainAxisAlignment.START
        )

        # Управление записью: Кнопки
        row_record: ft.Row = ft.Row(
            controls=[
                ft.Text(lcvt("0013|Управление записью:"), weight=ft.FontWeight.BOLD, width=170),
                self.btn_start,
                self.btn_stop,
                # self.btn_test,
                self.lbl_status,
                self.lbl_lag,
                self.chk_srver_stt,
                self.chk_record_to_file,
                self.btn_open_record_folder
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        result_row = self.result_view.get_ui()

        self.page.add(
            ft.Container(content=row_title, padding=10),
            ft.Container(content=row_record, padding=10),
            ft.Container(content=result_row, padding=10, expand=True),
        )

    async def post_init(self):
        await self.proc_queue.start_processing()

        self.config_manager.load()
        if not self.config_manager.config_ok:
            self.show_settings_dialog()

        if self.config_manager.config_ok:
            self.net_client.login(self.config_manager)

        self.main_status_refresh()

    def main_status_refresh(self):
        if not self.config_manager.config_ok:
            self.main_status.value = lcvt("0014|Введите настройки")
            self.main_status_widget.visible = True
            return

        if not self.net_client.is_login_ok:
            self.main_status.value = self.net_client.last_error
            self.main_status_widget.visible = True
            return

        self.main_status.value = ""
        self.main_status_widget.visible = False

    # async def test_btn_click(self) -> None:        
    #     await self.test_results_load()

    # async def test_results_load(self)->None:
    #     results = self.test_load_results()
    #     for item in results:
    #         await self.result_view.add_results([item], [])
    #         #await asyncio.sleep(1)

    # def test_load_results(self)->List[ResultSegment]:
    #     """Загружает тестовые данные из файла 'test_results.json'

    #     и возвращает список объектов ResultSegment.
    #     """
    #     # 1. Открываем файл для чтения с нужной кодировкой (для кириллицы)
    #     with open("test_results.json", "r", encoding="utf-8") as file:
    #         # 2. Читаем JSON и превращаем его в список словарей
    #         data_dicts = json.load(file)

    #     # 3. Превращаем каждый словарь в объект класса ResultSegment
    #     # Оператор ** распаковывает ключи словаря в аргументы класса
    #     results = [ResultSegment(**item) for item in data_dicts]

    #     return results

    async def init(self):
        self.init_ui()
        await self.post_init()
        self.page.update()

    # Функция для стандартной кнопоки сворачивания
    def minimize_window(self):
        self.page.window.minimized = True
        self.page.update()

    # Функция для стандартной кнопоки закрытия
    async def close_window(self):
        await self.clean_up()
        await self.page.window.close()

    async def open_self_site(self):
        url = f"{self.config_manager.base_url}/tokenin?token={self.config_manager.token}"
        await ft.UrlLauncher().launch_url(url)

    def show_settings_dialog(self):
        if self.settings_dialog == None:
            self.settings_dialog = SettingsDialog(
                page=self.page,
                config_manager=self.config_manager,
                net_client=self.net_client,
                on_success_auth=self._on_settings_input_ok
            )

        self.settings_dialog.open()

    def _on_settings_input_ok(self):
        self.main_status_refresh()

    def _on_row_change(self, _row: DeviceRow) -> None:
        record_possible = False

        for dev_row in self.dev_rows:
            if dev_row.audio_source:
                record_possible = True

        self.btn_start.disabled = not record_possible

    def _on_raw_chank(self, chunk: RawChunk) -> None:
        if self.recording_on and chunk.source_id in self.recording_source_ids:
            asyncio.create_task(self.file_saver.add_chunk(chunk))

    def _on_voice_chunk(self, chunk: VoiceChunk) -> None:
        """Callback: Буфер накопил и отдал VoiceChunk"""
        if self.recording_on and chunk.source_id in self.recording_source_ids:            
            asyncio.create_task(self.proc_queue.put(chunk))

    async def _on_encoded_chunk(self, packet: EncodedChunk) -> None:
        """Callback: Пакет сжат в OGG и готов к обработке"""
        source = next((source for source in self.recording_source_list if source.source_id == packet.source_id), None)
        if source == None: return

        self.audio_packet_num += 1        
        await self.net_client.send_packet(packet, self.audio_packet_num, source.diarize)

    def _on_file_upload_start(self) -> None:
        """Начало загрузки файла на сервер"""

        if not self._check_net_connection():
            return

        for dev_row in self.dev_rows:
            dev_row.chk_enable_device.value = False

        self.recording_source_list.clear()

        for channel in range(self.file_upload_row.channels_count):
            self.recording_source_list.append(RecordSourceInfo(channel, self.file_upload_row.chk_diarize.value==True))

        if not self.recording_source_list:
            return

        asyncio.create_task(self.start_recording_ex(True,lcvt("0015|Статус: загрузка файла на сервер")))
      

    def _on_file_finish(self) -> None:
        # Вызывается при получении всех результатов STT обработки        
        asyncio.create_task(self.stop_recording())

    def _check_net_connection(self) -> bool:
        if self.net_client.is_login_ok:
            return True

        if not self.config_manager.config_loaded:
            show_toast(self.page, lcvt("0016|Настройте подключение к серверу"))   
            return False

        if not self.net_client.login(self.config_manager):
            show_toast(self.page, self.net_client.last_error)
            return False

        return self.net_client.is_login_ok     

    async def start_recording(self) -> None:
        """Начало записи"""

        if self.chk_srver_stt.value and not self._check_net_connection():
            return
        
        if not self.chk_srver_stt.value and not self.chk_record_to_file.value:
            show_toast(self.page, f"{lcvt('0017|Выбирите хотя бы одну из возможностей:')}\n{self.chk_srver_stt.label}\n{self.chk_record_to_file.label}")
            return

        self.recording_source_list.clear()

        for dev_row in self.dev_rows:
            if dev_row.chk_enable_device.value:
                self.recording_source_list.append(RecordSourceInfo(dev_row.source_id, dev_row.chk_diarize.value==True))

        if not self.recording_source_list:
            return

        await self.start_recording_ex(self.chk_srver_stt.value,lcvt("0018|Статус: ЗАПИСЬ..."))

        for dev_row in self.dev_rows:
            dev_row.start_recording()        

        if self.chk_record_to_file.value:
            await self.file_saver.start_recording(self._get_record_file_path(), self.recording_source_ids, SAMPLE_RATE)        

    async def start_recording_ex(self, net: Optional[bool], status: str) -> None:
        self.recording_source_ids = {source.source_id for source in self.recording_source_list}

        if net:
            await self.net_client.start_recording(self.title.value)

        for dev_row in self.dev_rows:
            dev_row.set_disabled(True)

        self.file_upload_row.set_disabled(True)

        self.btn_start.disabled = True
        self.btn_stop.disabled = False
        self.title.disabled = True
        self.lbl_status.value = status
        self.lbl_status.color = ft.Colors.RED
        self.lbl_lag.visible = True
        self.result_view.clear()
        self.page.update()

        self.recording_on = True

    async def stop_recording(self) -> None:
        """Остановка записи и обработка остатков буфера."""
        for dev_row in self.dev_rows:
            dev_row.stop_recording()
            dev_row.set_disabled(False)

        self.file_upload_row.stop()
        self.file_upload_row.set_disabled(False)

        # Небольшая пауза, чтобы воркер успел обработать завершающие пакеты
        await asyncio.sleep(0.5)

        await self.file_saver._stop_recording()
        await self.net_client.stop_recording()

        self.btn_start.disabled = False
        self.btn_stop.disabled = True
        self.title.disabled = False
        self.lbl_status.value = lcvt("0019|Статус: Ожидание")
        self.lbl_status.color = ft.Colors.GREEN
        self.lbl_lag.visible = False
        self.page.update()
        self.recording_on = False
        self.recording_source_list.clear()
        self.recording_source_ids.clear()

    def _format_time(self, seconds: int) -> str:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        remaining_seconds = seconds % 60

        parts = []
        
        if hours > 0:
            parts.append(f"{hours} {lcvt('0020|ч.')}")
            
        if minutes > 0:
            parts.append(f"{minutes} {lcvt('0021|мин.')}")
            
        if remaining_seconds > 0 or not parts:
            parts.append(f"{remaining_seconds} {lcvt('0022|сек.')}")

        return " ".join(parts)

    def on_change_progress(self) ->None:
        lag_seconds = (self.net_client.sended_ms - self.net_client.result_ms) // 1000 
        self.lbl_lag.value = f"{lcvt('0023|Отставание:')} { self._format_time(lag_seconds) }"
        self.lbl_lag.update()
                
        if self.file_upload_row.is_active:
            self.file_upload_row.set_progress(self.net_client.sended_count, self.net_client.sended_ms, self.net_client.result_count, self.net_client.result_ms)

    def on_send(self):
        self.on_change_progress()

    def on_results(self, results: List[ResultSegment], spks: List[Speaker]):
        self.on_change_progress()

        asyncio.create_task(self.result_view.add_results(results, spks))

    def _get_record_file_path(self)->str:
        return "./test.ogg"

    @staticmethod
    def open_explorer(folder: str)->None:
        

        if current_os == "Windows":
            # Открывает проводник Windows (explorer)
            # заменяем слэши на обратные для Windows
            path = os.path.normpath(folder)
            subprocess.run(["explorer", path])

        elif current_os == "Linux":
            # Открывает стандартный файловый менеджер в Linux (xdg-open)
            subprocess.run(["xdg-open", folder])    

    def open_record_folder(self)->None:
        self.open_explorer("C:\\")

    def on_mic_add_click(self)->None:
        mic_src_id = max((dev.source_id for dev in self.dev_rows if dev.device_type == DeviceType.MICROPHONE), default=0) + 1
        self.dev_rows.append(
            DeviceRow(
                self.page, 
                DeviceType.MICROPHONE, mic_src_id, 
                self._on_raw_chank, 
                self._on_voice_chunk, 
                self._on_row_change, 
                None,
                self.dev_rows
            )
        )
        self.dev_rows.sort(key=lambda dev: dev.source_id)

        self.page.clean()
        self.init_ui()
        self.page.update()

    async def clean_up(self) -> None:
        """Очистка ресурсов при закрытии."""
        await self.proc_queue.stop_processing()

        for dev_row in self.dev_rows:
            await dev_row.clean_up()

        await self.file_saver.clean_up()


async def main(page: ft.Page) -> None:
    app: AudioRecorderApp = AudioRecorderApp(page)
    await app.init()

    # Регистрация хука на закрытие приложения для освобождения потоков
    page.on_close = app.clean_up
