from src.net.net_client import NetClient
import flet as ft
from src.models import *
import asyncio

from src.net.net_client import *
from src.audio.play_sound import *
from src.utils import show_confirm_dialog
from src.ui.stt_sessions_sel_dlg_ui import *
from src.translate import lcvt

from src.ui.llm.llm_text_processor import *
from src.ui.result_save_dlg_ui import *
from src.ui.input_text_dlg_ui import *

_RV: 'ResultView'

class ResultView:
    def __init__(
        self, 
        page: ft.Page,
        net_client: NetClient,
        on_select_session: OnSelectSttSession
    ) -> None:
        
        global _RV
        _RV = self
        
        self.page = page
        self.net_client = net_client
        self.on_select_session = on_select_session
        self.llmtp: LlmTextProcessor | None = None

        self.result_list: List[ResultSegment] = []
        self.lv_items: dict[str, '_LvItem'] = {} # ItemKey -> _LvItem
        self.spknmap: dict[str, str] = {} # spkid -> имя спикера
        self.key_num: int = 0
        self.selected: List[str] = []
        self.net_spknames: List[str] = []
        self.spks: dict[str, Speaker] = {}

        self.spknum_illegible = "/999999" # номер зарезервирован для..
        self.spk_illegible_title = lcvt("0226|ничего не разобрать")

        self.lv = ft.ListView(        
            expand=True, 
            scroll=ft.ScrollMode.ALWAYS,
        )

        asyncio.create_task(self._get_net_speakers()) # Получаем список спикеров пользователя

    async def _get_net_speakers(self) -> None:
        self.net_spknames = await self.net_client.get_speakers()

    def get_ui(self) -> ft.Control:
        """Возвращает виджет, который будет встроен в форму."""
        results = ft.Container(
            expand=True,
            padding=ft.Padding.all(10),
            content=self.lv,
            border=ft.Border.all(width=1, color=ft.Colors.OUTLINE),
            border_radius=ft.BorderRadius.all(4),
        )

        vtoolbar = ft.Column(
            controls=[
                ft.IconButton(icon=ft.Icons.COPY, on_click=self.btn_copy_click, tooltip=lcvt("0201|Копировать текст в буффер обмена")), 
                ft.IconButton(icon=ft.Icons.ARTICLE, on_click=self.btn_text_view_click, tooltip=lcvt("0202|Просмотр текста")),
                ft.IconButton(icon=ft.Icons.VOLUME_UP, on_click=self.btn_paly_click, tooltip=lcvt("0203|Воспроизвести запись")),    
                ft.IconButton(icon=ft.Icons.AUTORENEW, on_click=self.btn_reproc_click, tooltip=lcvt("0204|Повторить обработку")),                 
                ft.IconButton(icon=ft.Icons.AUTO_AWESOME, on_click=self.btn_llm_click, tooltip=lcvt("0205|ИИ Обработка текста")), 
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, on_click=self.btn_clear_click, tooltip=lcvt("0206|Отчистить результаты")), 
                ft.IconButton(icon=ft.Icons.FILE_OPEN, on_click=self.btn_stts_click, tooltip=lcvt("0207|Загрузить результаты другой записи")),
            ]
        )

        return ft.Row(controls=[
            results,
            vtoolbar
        ])
    
    def clear(self) -> None:
        """Отчищает данные - подготовка к новой сессии"""
        self.result_list.clear()
        self.spknmap.clear()
        self.selected.clear()   

    def get_results(self) -> List[ResultSegment]:
        if not self.selected:
            return self.result_list
        
        result: List[ResultSegment] = []
        for item in self.result_list:
            ikey = self._get_item_key(item)
            if ikey in self.selected:
                result.append(item)
        
        return result                   

    def get_text(self)->str:
        return result_get_text(TextFormat.text, self.get_results(), self.spknmap)

    async def btn_paly_click(self):
        if not self.selected:
            show_toast(self.page, lcvt("0208|Сначала выбирите записи"))
            return

        ab_cid: int = -1
        audio_buffer: BytesIO | None = None

        cur_cid: int = -1
        cur_cid_tss: int = 0

        for result in self.result_list:
            if cur_cid != result.cid:
                cur_cid = result.cid
                cur_cid_tss = result.tss

            ikey = self._get_item_key(result)
            if not ikey in self.selected: continue

            if result.cid != ab_cid:
                ab_cid = result.cid
                audio_buffer = await self.net_client.get_proced_audio(ab_cid)

            if audio_buffer is None: continue

            from_sec = (result.tss - cur_cid_tss) / 1000
            to_sec = (result.tse - cur_cid_tss) / 1000
            await play_sound_from_buffer(audio_buffer, from_sec, to_sec)

    async def btn_reproc_click(self):
        if not self.selected:
            show_toast(self.page, lcvt("0209|Сначала выбирите записи"))
            return

        show_confirm_dialog(
            self.page,
            lcvt("0210|Предупреждение"),
            lcvt("0211|Отправить выбранное на повторную обработку ?"),
            on_yes= self._reproc_start
        )

    def _reproc_start(self)->None:
        cids = list({ikey.split('|')[0] for ikey in self.selected})
        for cid in cids:
            asyncio.create_task(self.net_client.reset_audio(int(cid)))        

    async def btn_clear_click(self):
        show_confirm_dialog(
            self.page,
            lcvt("0212|Предупреждение"),
            lcvt("0213|Хотите удалить результаты?"),
            on_yes= self.clear_results
        )

    async def btn_stts_click(self):
        stts_list = await self.net_client.get_sessions()
        stts = await select_stt_session(self.page, stts_list)
        if stts is None: return
    
        self.net_client.current_sid = stts.sid
        stts_results, stts_spks = await self.net_client.get_session_data(stts.sid)

        self.result_list.clear()
        await self.add_results(stts_results, stts_spks)

        self.on_select_session(stts)

    def clear_results(self)->None:
        self.result_list.clear()
        self.spks.clear()
        self.spknmap.clear()
        self.selected.clear()        
        asyncio.create_task( self.refresh_result(False))

    async def btn_llm_click(self):
        if not self.llmtp:
            self.llmtp = LlmTextProcessor(self.page, self.net_client)

        text = self.get_text()
        await self.llmtp.open(text)

    async def btn_copy_click(self):
        text = self.get_text()
        await ft.Clipboard().set(text)

    async def add_results(self, results: List[ResultSegment], spks: List[Speaker]):
        if not results:
            return

        cids = list({result.cid for result in results})

        self.result_list = [result for result in self.result_list if not result.cid in cids]
        
        self.result_list.extend(results)
        self.result_list.sort(key=lambda x: x.tss)
        
        for spk in spks:
            self.spks[spk.spkid] = spk

        for result in results:
            if not self.spks.get(result.spkid):
                self.spks[result.spkid] = Speaker(
                    spkid=result.spkid,
                    title=self.spk_illegible_title if result.spkid.endswith(self.spknum_illegible) else result.spkid,
                    ref_spkid="",
                    is_good=False,
                    is_bad=True
                )

        self._refresh_speaker_names()

        await self.refresh_result(True)   

    def _refresh_speaker_names(self) ->None:
        # Заполняем имена спикеров
        self.spknmap.clear()

        spks_values = self.spks.values()

        for spk in spks_values:
            if spk.title != "" and spk.ref_spkid != "" and spk.ref_spkid != spk.spkid:
                title= self.spknmap.get(spk.ref_spkid)
                if title is None:
                    self.spknmap[spk.ref_spkid] = spk.title
                elif spk.title != title:
                    self.spknmap[spk.ref_spkid] = "*"

        for spk in spks_values:
            if spk.title != "":                
                self.spknmap[spk.spkid] = spk.title

        for spk in spks_values:
            if spk.title == "" and spk.ref_spkid != "" and spk.ref_spkid != spk.spkid:
                title= self.spknmap.get(spk.ref_spkid)
                if title is not None:
                    self.spknmap[spk.spkid] = title

        for spk in self.spks.values():
            if spk.spkid not in self.spknmap:
                self.spknmap[spk.spkid] = spk.spkid

        for spkid, title in self.spknmap.items():
            if title == "*":
                self.spknmap[spkid] = spkid           

    async def refresh_result(self, scroll: bool):
        self.lv.auto_scroll=scroll
        self.lv.controls.clear()
        last_index = len(self.result_list) - 1
        new_exists = False
        for index, item in enumerate(self.result_list):
            spkname = self.spknmap[item.spkid]

            first = index == 0 or self.spknmap[self.result_list[index - 1].spkid] != spkname
            last  = index == last_index or self.spknmap[self.result_list[index + 1].spkid] != spkname

            ikey = self._get_item_key(item)

            if ikey in self.selected:
                color = ft.Colors.GREEN
            else:
                color = ft.Colors.BLUE

            lv_item = self.lv_items.get(ikey)

            if lv_item is None:
                lv_item = _LvItem(
                    is_first = first,
                    is_last  = last,
                    color    = color,
                    spkname  = spkname,
                    item     = item,
                    text     = item.text,
                    ikey     = ikey,
                    column   = ft.Column(key = ikey)
                )
                self.lv_items[ikey] = lv_item
                lv_item.refresh_row()
                new_exists = True
            else:
                lv_item.if_change(
                    is_first = first,
                    is_last  = last,
                    color    = color,
                    spkname  = spkname,
                    text     = item.text                    
                )

            self.lv.controls.append(lv_item.column)

        if new_exists:
            self.lv.update()

    def _get_item_key(self, item: ResultSegment) -> str:
        return f"{item.cid}|{item.tse}"

    async def on_tap_item(self, item: ResultSegment):
        ikey = self._get_item_key(item)
        if ikey in self.selected:
            self.selected.remove(ikey)
        else:
            self.selected.append(ikey)
        await self.refresh_result(False)

    async def on_tap_head(self, item:ResultSegment):
        ikey = self._get_item_key(item)
        bitems: List[str] = [ikey]
        ispk_name: str = self.spknmap[item.spkid]
        found: bool = False
        
        for xitem in self.result_list:
            if not found:
                xkey = self._get_item_key(xitem)
                if xkey == ikey:
                    found = True
            else:
                xspk_name: str = self.spknmap[xitem.spkid]
                if xspk_name != ispk_name:
                    break
                bitems.append(self._get_item_key(xitem))


        if ikey in self.selected:
            self.selected = [item for item in self.selected if item not in bitems]
        else:
            self.selected.extend(bitems)

        await self.refresh_result(False)        

    def set_speaker_name(self, spkid: str, new_name: str)->None:
        self.spknmap[spkid] = new_name

        if self.spks[spkid].is_good:
            asyncio.create_task(self.net_client.set_speaker_title(spkid, new_name))
            
        asyncio.create_task(self.refresh_result(False))

    async def btn_text_view_click(self):
        if not self.result_list: return

        results = self.get_results()

        if len(results) != 1:
            result_save_dlg(self.page, results, self.spknmap)
            return

        result = results[0]
        text = await input_text_dlg(self.page, "Редактирование", self.spknmap[result.spkid], "", result.text)
        if text:
            result.text = text
            await self.refresh_result(True)

@dataclass
class _LvItem:
    is_first: bool
    is_last: bool
    color: str
    spkname: str
    item: ResultSegment
    text: str
    ikey: str
    column: ft.Column

    def refresh_row(self) -> None:
        self.column.controls.clear()

        # Настройка скруглений для контейнера
        top_radius = 12 if self.is_first else 0
        bottom_radius = 12 if self.is_last else 0
        border_radius = ft.BorderRadius.only(
            top_left=top_radius, top_right=top_radius,
            bottom_left=bottom_radius, bottom_right=bottom_radius
        )

        content_controls:List[ft.Control] = []

        if self.is_first:
            speaker_row = self._get_speaker_row(self.item)
            content_controls.append(speaker_row)

        segment_text = ft.GestureDetector(
            content=ft.Row(controls=[ft.Text(value=self.item.text, size=16, expand=True)])
        )             

        content_controls.append(segment_text)
         
        result = ft.Container(
                content=ft.Column(
                    controls=content_controls, 
                ),
                border_radius=border_radius,
                padding=ft.Padding.only(left=10, right= 10, top = 10 if self.is_first else 0, bottom= 10 if self.is_last else 0),
                bgcolor=self.color,
            )

        async def on_tap():
            await _RV.on_tap_item(self.item)

        segment_text.on_tap = on_tap    

        if not self.is_last:
            self.column.controls.append(result)
        else:
            self.column.controls.append(ft.Container(
                content=result,
                padding=ft.Padding.only(bottom=4),
            ))

    def if_change(self, is_first: bool, is_last: bool, color: str, spkname: str, text: str) ->None:
        if self.is_first == is_first and self.is_last == is_last and self.color == color and self.text == text:
            if self.spkname == spkname:
                return

            self.spkname = spkname
            if not self.is_first:
                return

        self.is_first = is_first 
        self.is_last  = is_last 
        self.color    = color
        self.text     = text

        self.refresh_row()
        if self.column.parent is not None:
            self.column.update()

    def _get_speaker_row(self, item: ResultSegment)->ft.Row:
        row = ft.Row()
        self._set_speaker_row_simple(item, row)
        return row
    
    def _set_speaker_row_simple(self, item: ResultSegment, row: ft.Row):
        async def on_tap():
            await _RV.on_tap_head(self.item)

        def on_edit_click():
            self._set_speaker_row_edit(item, row)
            row.update()            
    
        text = ft.GestureDetector(
            content=ft.Text(value=self.spkname, size=16),
            expand=True,
            on_tap=on_tap
        )             

        time = ft.Text(value=self._format_time(item.tss), size=16)

        edit_btn=ft.GestureDetector(
            content=ft.Icon(icon=ft.Icons.EDIT),
            on_tap=on_edit_click
        )        

        row.controls.clear()
        row.controls.append(time)
        row.controls.append(text)
        row.controls.append(edit_btn)

    def _format_time(self, time: int) -> str:
        # Переводим миллисекунды в секунды
        total_seconds = time // 1000
        
        seconds = total_seconds % 60
        total_minutes = total_seconds // 60
        minutes = total_minutes % 60
        hours = total_minutes // 60
        
        # Форматируем строку в зависимости от наличия часов и минут
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        elif minutes > 0:
            return f"{minutes}:{seconds:02d}"
        else:
            return f"0:{seconds:02d}"        

    def _set_speaker_row_edit(self, item: ResultSegment, row: ft.Row):
        index = _RV.result_list.index(item)

        speaker_field = ft.TextField(
            value=self.spkname,
            hint_text=lcvt("0219|Имя спикера"),
            expand=True
        )

        def on_ok_click():
            new_name = speaker_field.value
            if new_name:
                _RV.set_speaker_name(item.spkid, new_name)

        def on_cancel_click():
            self._set_speaker_row_simple(item, row)
            row.update() 

        def on_up_click():
            spkid = _RV.result_list[index - 1].spkid
            speaker_field.value = _RV.spknmap[spkid]
            speaker_field.update()            

        def on_down_click():
            spkid = _RV.result_list[index + 1].spkid
            speaker_field.value = _RV.spknmap[spkid]            
            speaker_field.update()  

        def on_speaker_selected(e: ft.Event[ft.PopupMenuItem]) -> None:
            speaker_field.value = e.control.data 
            speaker_field.update()

        jspknames = sorted(set(_RV.spknmap.values()))

        btn_sel_int = ft.PopupMenuButton(
                icon=ft.Icons.HOW_TO_REG,  
                menu_position= ft.PopupMenuPosition.UNDER,
                tooltip=lcvt("0220|Выбрать из присвоеных спикеров"),
                items=[
                    ft.PopupMenuItem(
                        content=name,
                        data=name,                        
                        on_click=on_speaker_selected,  
                    )
                    for name in jspknames
                ],
            )               

        btn_sel_spk = ft.PopupMenuButton(
                icon=ft.Icons.PEOPLE_ALT,  
                menu_position= ft.PopupMenuPosition.UNDER,
                tooltip=lcvt("0221|Выбрать из сохранённого списка спикеров"),
                items=[
                    ft.PopupMenuItem(
                        content=name,
                        data=name,                       
                        on_click=on_speaker_selected,  
                    )
                    for name in _RV.net_spknames
                ],
            )      

        btn_up = ft.IconButton(
            icon=ft.Icons.ARROW_UPWARD, 
            tooltip=lcvt("0222|Взять сверху"),
            on_click=on_up_click
        )

        btn_down = ft.IconButton(
            icon=ft.Icons.ARROW_DOWNWARD, 
            tooltip=lcvt("0223|Взять снизу"),
            on_click=on_down_click
        )
        
        if index == 0:
            btn_up.visible = False

        if index + 1 == len(_RV.result_list):
            btn_down.visible = False

        btn_ok = ft.IconButton(
            icon=ft.Icons.CHECK, 
            tooltip=lcvt("0224|Подтвердить изменение"),
            on_click=on_ok_click
        )

        btn_cancel = ft.IconButton(
            icon=ft.Icons.CANCEL, 
            tooltip=lcvt("0225|Отменить изменение"),
            on_click=on_cancel_click
        )        

        row.controls.clear()
        row.controls.append(speaker_field)
        row.controls.append(btn_sel_int)
        row.controls.append(btn_sel_spk)
        row.controls.append(btn_up)
        row.controls.append(btn_down)
        row.controls.append(ft.Container(width=20))
        row.controls.append(btn_ok)
        row.controls.append(btn_cancel)        
