from __future__ import annotations

import flet as ft


from src.audio.my_audio import *
from src.utils import *
from src.audio.play_sound import *
from src.translate import lcvt


class DeviceRow:
    def __init__(
            self,
            page: ft.Page,
            device_type: DeviceType,
            source_id: int,
            on_raw_chunk: OnRawChunk,
            on_voice_chunk: OnVoiceChunk,
            on_change: Callable[[DeviceRow], None],
            on_mic_add: Optional[SimpleCallBack],
            dev_rows: List[DeviceRow]
    ) -> None:
        self.page: ft.Page = page
        self.device_type = device_type
        self.source_id = source_id
        self.on_raw_chunk: OnRawChunk = on_raw_chunk
        self.on_change: Callable[[DeviceRow], None] = on_change
        self.dev_rows: List[DeviceRow] = dev_rows

        self.buffer: VoiceBuffer = VoiceBuffer(self.source_id, on_voice_chunk)
        self.audio_source: Optional[AudioSource] = None

        self.is_recording = False

        # GUI элементы
        self.chk_enable_device: ft.Checkbox = ft.Checkbox(value=False, on_change=self.toggle_device)
        self.dd_devices: ft.Dropdown = ft.Dropdown(expand=True, on_select=self.on_device_changed)
        self.pb_activity: ft.ProgressBar = ft.ProgressBar(width=150, value=0.0, color=ft.Colors.GREEN)
        self.chk_diarize: ft.Checkbox = ft.Checkbox(label=lcvt("0101|Разделить говорящих"), value=False, label_position=ft.LabelPosition.LEFT)

        self.btn_last: ft.AdaptiveControl
        
        if self.device_type == DeviceType.LOOPBACK:
            self.chk_diarize.value = True
            self.btn_last = ft.IconButton(
                icon=ft.Icons.VOLUME_UP,
                on_click=self.on_play_sound_click,
                tooltip=lcvt("0102|Проверка звука в динамиках")
            )

        if self.device_type == DeviceType.MICROPHONE:
            self.chk_diarize.value = False
            if on_mic_add is not None:                
                self.btn_last = ft.IconButton(
                    icon=ft.Icons.ADD,
                    on_click=on_mic_add,
                    tooltip=lcvt("0103|Добавить ещё один микрофон")
                ) 
            else:
                self.btn_last = ft.Container(width=40)

        # --- Настройки для динамического gain ---
        self.current_gain: float = 1.0
        self.target_rms: float = 0.15  # Желаемый средний уровень RMS (к какому уровню стремимся)
        self.min_gain: float = 0.5  # Ограничение снизу (чтобы при крике не падало в 0)
        self.max_gain: float = 20.0  # Ограничение сверху (чтобы в тишине не ловить шумы)

        # Коэффициенты скорости адаптации (значения от 0.0 до 1.0)
        # Чем меньше значение, тем медленнее меняется gain
        self.attack_speed: float = 0.2  # Скорость уменьшения gain при резком громком звуке (быстро)
        self.decay_speed: float = 0.02  # Скорость увеличения gain при тихом звуке (медленно)

    def get_row_ui(self) -> ft.Row:
        label = lcvt("0104|Динамик:") if self.device_type == DeviceType.LOOPBACK else lcvt("0105|Микрофон:")
        row: ft.Row = ft.Row(
            controls=[
                ft.Text(label, weight=ft.FontWeight.BOLD, width=85),
                self.chk_enable_device,
                self.dd_devices,
                ft.Text(lcvt("0106|Активность:")),
                self.pb_activity,
                self.chk_diarize,
                self.btn_last
            ],
            alignment=ft.MainAxisAlignment.START
        )

        devices: List[AudioDevice] = audio_devices.get_audio_devices(self.device_type)
        self._refresh_audio_devices(devices)

        audio_devices.reg_change_list_callback(self.device_type, self._on_change_audio_devices_list)

        return row
    
    async def toggle_device(self) -> None:
        """Включение/выключение использования аудио-устройства."""
        if self.chk_enable_device.value and self.dd_devices.value is not None:
            selected_device_id: str = self.dd_devices.value

            self.audio_source = AudioSource(
                device_type=self.device_type,
                device_id=selected_device_id,
                chunk_size=RAW_CHANK_SIZE,
                on_chunk=self.on_chunk,
                on_break=self.on_device_break
            )
            await self.audio_source.start()
        else:
            if self.audio_source:
                await self.audio_source.stop()
                self.audio_source = None

        self.pb_activity.value = 0.0

        self.on_change(self)
        self.page.update()

    async def on_device_changed(self) -> None:
        """Смена устройства в списке."""
        # Если выбранное устройство уже присутствует в другой строке - убираем его
        for devr in self.dev_rows:
            if devr.device_type == self.device_type and devr.dd_devices.value == self.dd_devices.value and devr.source_id != self.source_id:
                devr.dd_devices.value = None
                await devr.on_device_changed()

        if self.chk_enable_device.value:
            # Переинициализируем поток, если устройство поменяли "на лету"
            self.chk_enable_device.value = False
            await self.toggle_device()            

    def on_chunk(self, mtime: int, data: np.ndarray) -> None:
        self.on_raw_chunk(RawChunk(self.source_id, mtime, data))

        is_speach: bool = False

        if self.is_recording:
            # Если включена запись — отправляем данные в буфер:
            is_speach = self.buffer.add_samples(data, mtime)
        else:
            is_speach = self.buffer.is_voice(data)

        rms: float = self.calc_rms(data)
        gain: float = 1

        # Рассчитываем gain только во время речи, 
        # чтобы в паузах индикатор не улетал в максимум из-за шума электроники
        if is_speach:
            gain = self.calc_speach_gain(rms)
        else:
            # Если речи нет, плавно возвращаем gain к базовому значению 1.0
            self.current_gain += (1.0 - self.current_gain) * 0.05
            gain = self.current_gain

        visual_value = rms * gain * (1.0 / self.target_rms) * 0.5
        self.pb_activity.value = np.clip(visual_value, 0.0, 1.0)
        self.page.update()

    def calc_speach_gain(self, rms: float) -> float:
        if rms < 0.001:
            return self.current_gain

        ideal_gain = self.target_rms / rms

        ideal_gain = np.clip(ideal_gain, self.min_gain, self.max_gain)

        if ideal_gain < self.current_gain:
            # Звук стал громче -> мгновенно или быстро уменьшаем gain (Attack)
            self.current_gain += (ideal_gain - self.current_gain) * self.attack_speed
        else:
            # Звук стал тише -> медленно поднимаем gain (Decay)
            self.current_gain += (ideal_gain - self.current_gain) * self.decay_speed

        return self.current_gain

    @staticmethod
    def calc_rms(data: np.ndarray) -> float:
        # Расчет интегральной громкости (RMS) для индикатора активности
        rms: float = float(np.sqrt(np.mean(data ** 2)))
        return rms

    def _on_change_audio_devices_list(self, devices: List[AudioDevice]) -> None:
        self._refresh_audio_devices(devices)
        self.page.update()

    def _refresh_audio_devices(self, devices: List[AudioDevice]) -> None:
        """Заполнение выпадающего списка звуковыми картами."""

        if self.dd_devices.value is not None and not any(dev for dev in devices if dev.id == self.dd_devices.value):
            self.dd_devices.value = None

        set_default = self.dd_devices.value is None and self.device_type != DeviceType.MICROPHONE or self.source_id == 1        

        self.dd_devices.options.clear()
        for dev in devices:
            self.dd_devices.options.append(ft.DropdownOption(key=dev.id, text=dev.name))

        if set_default and devices:
            # Выставляем устройство по умолчанию
            self.dd_devices.value = devices[0].id

        not_has_value = self.dd_devices.value is None

        self.chk_enable_device.disabled = not_has_value
        self.chk_diarize.disabled = not_has_value
        self.btn_last.disabled = not_has_value
        self.pb_activity.value = 0.0

        if not_has_value:
            if self.chk_enable_device.value: # Было включено -> выключаем
                self.chk_enable_device.value = False
                asyncio.create_task(self.toggle_device())

    def on_device_break(self) -> None:
        self.chk_enable_device.value = False
        show_toast(self.page, f"{lcvt('0107|Сбой в работе устройства')} {self.dd_devices.text}") 
        self.stop_recording()
        asyncio.create_task(self.toggle_device())

    def set_disabled(self, disabled:bool)-> None:
        self.chk_enable_device.disabled = disabled 
        self.dd_devices.disabled = disabled 

    def start_recording(self) -> None:
        if not self.chk_enable_device.value:
            return
        
        self.is_recording = True
        self.buffer.start_recording()

    def stop_recording(self) -> None:
        if not self.is_recording:
            return

        self.buffer.flush()

        self.is_recording = False

    async def on_play_sound_click(self)->None:
        if not audio_devices.loopback_exists: return
        
        self.chk_enable_device.value = True
        await self.toggle_device()
        play_test_sound()

    async def clean_up(self) -> None:
        """Очистка ресурсов при закрытии."""
        audio_devices.unreg_change_list_callback(self.device_type, self._on_change_audio_devices_list)

        if self.audio_source:
            await self.audio_source.stop()

        if self.buffer:
            self.buffer.reset()

