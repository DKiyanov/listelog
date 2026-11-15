import flet as ft

from src.models import *
from src.file.file_reader import *
from src.translate import lcvt

class FileUploadRow:
    def __init__(
        self, 
        page: ft.Page,
        on_start: SimpleCallBack,
        on_voice_chunk: OnFragment,
    ) -> None:        
        self.page = page

        self.on_start_parent: SimpleCallBack = on_start
        self.on_voice_chunk: OnFragment = on_voice_chunk

        self.file_reader = FileReader(self._on_chunk)
        self.buffers: dict[int, VoiceBuffer]

        self.is_active = False
        self.packet_count: int = 0

        self.channels_count = 0
        self.duration: int = 0    
        self.total_duration: int = 0 # self.channels_count * self.duration
        
        # Создаем FilePicker для выбора медиафайлов
        self.file_picker = ft.FilePicker()
        self._fconv_dialog: ft.AlertDialog | None = None
        
        # Основные элементы управления (Верхняя строка)
        self.file_path_input = ft.TextField(
            hint_text=lcvt("0301|Выберите медиафайл..."),
            expand=True,
            read_only=True,
            suffix_icon=ft.GestureDetector(
                content=ft.Icon(
                    icon=ft.Icons.FOLDER_ROUNDED,
                    color=ft.Colors.YELLOW,
                    tooltip=lcvt("0302|Выбор файла для загрузки")
                ),
                on_tap=self._on_browse_click
            )
        )

        self.upload_btn = ft.IconButton(
            icon=ft.Icons.UPLOAD,
            icon_color=ft.Colors.BLUE,
            on_click=self._on_upload_click,
            disabled=True,
            tooltip=lcvt("0303|Загрузить файл на сервер")
        )

        self.chk_mono: ft.Checkbox = ft.Checkbox( label= lcvt("0304|В моно"), value=True, label_position=ft.LabelPosition.LEFT)
        self.chk_diarize: ft.Checkbox = ft.Checkbox( label= lcvt("0305|Разделить говорящих"), value=True, label_position=ft.LabelPosition.LEFT)
        
        # Элементы прогресса (изначально скрыта)
        self.progress_bar = ft.ProgressBar(value=0.0, expand=True)
        
        self.send_progress_row = ft.Row(
            controls=[self.progress_bar],
            visible=False
        )
        
        # Флаг для отслеживания прерывания таски загрузки
        self.upload_task = None

    def get_ui(self) -> ft.Column:
        """Возвращает виджет, который будет встроен в форму."""
        return ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(lcvt("0306|Из файла:"), weight=ft.FontWeight.BOLD, width=135),
                        self.file_path_input,
                        ft.Container(
                            width=238, 
                            content= ft.Row(controls=[self.chk_mono], alignment=ft.MainAxisAlignment.CENTER ), 
                        ),                        
                        self.chk_diarize,
                        self.upload_btn
                    ]
                ),
                self.send_progress_row
            ]
        )

    def _on_upload_click(self, e):
        #show_toast(self.page, "Выполняется конвертация формата аудио")
        self._fconv_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(lcvt("0307|Пожалуйста, подождите")),
            content=ft.Row(
                controls=[
                    ft.ProgressRing(), # Индикатор загрузки
                    ft.Text(lcvt("0308|Выполняется конвертация формата аудио")),
                ],
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )     

        self.file_path = self.file_path_input.value
        self.in_mono = self.chk_mono.value == True
        self.diarize = self.chk_diarize.value == True
        self.file_reader.set_file(self.file_path, self.in_mono)
        self.channels_count = self.file_reader.channels_count
        self.duration = int(self.file_reader.duration_sec * 1000)
        self.total_duration = self.channels_count * self.duration 
        self.buffers = {i: VoiceBuffer(source_id = i, on_fragment= self._on_self_fragment) for i in range(self.channels_count)}
        self.packet_count = 0        

        assert self._fconv_dialog
        self.page.show_dialog(self._fconv_dialog) 

        self.on_start_parent()       

    def start_read(self) -> asyncio.Future[Any]:
        self.is_active = True
        self.send_progress_row.visible = True
        self.send_progress_row.update()
        future = self.file_reader.start_read()
        return future

    async def _on_chunk(self, chanel: int, chunk: np.ndarray, mtime: int) -> None:
        buffer = self.buffers[chanel]
        buffer.add_samples(chunk, mtime)
        await asyncio.sleep(0) # даём время на обработку очереди

    def _on_self_fragment(self, voice_chunk: Fragment)-> None:
        self.packet_count += 1
        self.on_voice_chunk(voice_chunk)
        
        if self._fconv_dialog is not None:
            self._fconv_dialog.open = False
            self.page.update()
            self._fconv_dialog = None
            
    async def _on_browse_click(self, e):
        # Открываем диалог выбора файлов (фильтр на аудио и видео)
        result = await self.file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["mp3", "wav", "ogg", "flac", "m4a", "aac", "mp4", "mkv", "avi", "mov", "wmv", "flv"]
        )
        if result and result[0].path:      
            self.file_path_input.value = result[0].path
            self.upload_btn.disabled = False        
        else:
            self.file_path_input.value = ""
            self.upload_btn.disabled = True  
        self.page.update()  

    def stop(self)->None:
        if not self.is_active:
            return

        self.is_active = False
        self.file_path_input.value = ""
        self.file_reader.cancel_reading()

    def set_progress(self, sended_packet_count: int, sended_ms: int, result_count: int, result_ms: int) ->None:          
        sd_value = sended_ms/self.total_duration
        #print(f"sended_packet = {sended_packet_count}; sended_ms = {sended_ms}")

        # if self.packet_count > 0 and self.packet_count == sended_packet_count:
        #     sd_value = 1.0
        
        self.progress_bar.value = sd_value

        self.progress_bar.update()

    def set_disabled(self, disabled:bool)-> None:
        self.file_path_input.disabled = disabled 
        self.upload_btn.disabled = disabled   
        
        # Показываем строки прогресса
        self.send_progress_row.visible = disabled and self.is_active
        self.progress_bar.value = 0.0
