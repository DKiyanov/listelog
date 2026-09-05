from src.net.net_client import NetClient
import flet as ft
from src.models import *
import asyncio

from src.net.net_client import *
from src.audio.play_sound import *
from src.utils import show_confirm_dialog
from src.ui.stt_sessions_sel_dlg_ui import *
from src.translate import lcvt

_RV: 'ResultView'

class ResultView:
    def __init__(
        self, 
        page: ft.Page,
        net_client: NetClient
    ) -> None:
        
        global _RV
        _RV = self
        
        self.page = page
        self.net_client = net_client

        self.result_list: List[ResultSegment] = []
        self.lv_items: dict[str, '_LvItem'] = {} # ItemKey -> _LvItem
        self.spknmap: dict[str, str] = {} # spkid -> имя спикера
        self.key_num: int = 0
        self.selected: List[str] = []
        self.net_spknames: List[str] = []
        self.spks: dict[str, Speaker] = {}

        self.spk_symbol = "🧑"

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

    def get_text(self)->str:
        text: str = ""
        spk_name: str = "-"

        for item in self.result_list:
            if self.selected:
                ikey = self._get_item_key(item)
                if not ikey in self.selected:
                    continue

            item_spk_name = self.spknmap[item.spkid]

            if spk_name != item_spk_name:
                spk_name = item_spk_name
                text += f"\n {self.spk_symbol} {spk_name}"

            text += f"\n{item.text}"
        
        return text

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
        select_stt_session(self.page, stts_list, self.set_stts)
    
    def set_stts(self, stts: SttSession)->None:
        asyncio.create_task(self.set_stts_async(stts))

    async def set_stts_async(self, stts: SttSession)->None:
        self.net_client.current_sid = stts.sid
        stts_results, stts_spks = await self.net_client.get_session_data(stts.sid)

        self.result_list.clear()
        await self.add_results(stts_results, stts_spks)

    def clear_results(self)->None:
        self.result_list.clear()
        asyncio.create_task( self.refresh_result(False))

    async def btn_llm_click(self):
        print("LLM click")

    async def btn_copy_click(self):
        text = self.get_text()
        await ft.Clipboard().set(text)

    async def add_results(self, results: List[ResultSegment], spks: List[Speaker]):
        if not results:
            return

        cid = results[0].cid

        self.result_list = [result for result in self.result_list if result.cid != cid]
        
        self.result_list.extend(results)
        self.result_list.sort(key=lambda x: x.tss)

        for spk in spks:
            self.spks[spk.spkid] = spk

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
                    spkname  = spkname                    
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

    def btn_text_view_click(self):
        text = self.get_text()

        def on_text_show_result(text:str) -> None:
            sel_item: ResultSegment | None = None

            for item in self.result_list:
                if self.selected:
                    ikey = self._get_item_key(item)
                    if ikey in self.selected:
                        sel_item = item
                        break
            if not sel_item: return

            lines = text.split("\n")
            lines.append(f"{self.spk_symbol} ")

            spks: str = ""
            spkl: List[str] = []
            first: bool = True

            for line in lines:
                if line.startswith(f"{self.spk_symbol} "):
                    if spkl:
                        spkt = "\n".join(spkl)
                        if first:
                            first = False
                            sel_item.text = spkt
                        else:
                            new_spkid = f"{sel_item}@{sel_item.tss}"
                            self.spknmap[new_spkid] = spks
                            self.result_list.append(ResultSegment(
                                cid   = sel_item.cid,
                                tss   = sel_item.tss + 1,
                                tse   = sel_item.tse,
                                spkid = new_spkid,
                                text  = spkt
                            ))

                    spkl.clear()
                    spks = line[2:]
                else:
                    spkl.append(line)

            self.result_list.sort(key=lambda x: x.tss)

            asyncio.create_task(self.refresh_result(True))

        self._text_show(text, on_text_show_result if len(self.selected) == 1 else None)

    def _text_show(self, text: str, result_callback: Callable[[str], None] | None):
        selection = {"start": -1, "end": -1}

        def on_selection_change(e: ft.TextSelectionChangeEvent[ft.TextField]) -> None:
            if e.control.selection is None: return  
            selection["start"] = e.control.selection.start 
            selection["end"] = e.control.selection.end

        # Создаем многострочное поле ввода
        text_input = ft.TextField(
            value=text,
            multiline=True,
            expand=True,
            autofocus=True,
            border=ft.InputBorder.NONE,
            on_selection_change=on_selection_change
        )

        text_input.on_selection_change

        def close_dialog():
            dialog.open = False
            self.page.update()

        def on_cancel(e):
            close_dialog()

        def on_ok(e):
            if result_callback:
                result_callback(text_input.value)
            close_dialog()

        def on_copy(e):
            full_text = text_input.value or ""
            start = selection["start"]
            end  = selection["end"]

            # Если индексы не равны, значит было активное выделение
            if start != end:
                start = min(start, end)
                end = max(start, end)
                selected_text = full_text[start:end]
            else:
                selected_text = full_text     

            if selected_text:
                asyncio.create_task(ft.Clipboard().set(selected_text))

        def on_speaker_selected(e: ft.Event[ft.PopupMenuItem]):
            current_text = text_input.value or ""
            cursor_pos: int = selection["start"]
            cursor_pos -= 1
            if cursor_pos < 0: return
            if cursor_pos >= len(current_text): return

            spk_name = e.control.data
            text_to_insert = f"\n{self.spk_symbol} {spk_name}\n" 
                            
            new_text = current_text[:cursor_pos].strip() + text_to_insert + current_text[cursor_pos:].strip()
            text_input.value = new_text
            text_input.update()

        jspknames = sorted(set(self.spknmap.values()))

        btn_sel_int = ft.PopupMenuButton(
                icon=ft.Icons.HOW_TO_REG,  
                menu_position= ft.PopupMenuPosition.UNDER,
                tooltip=lcvt("0214|Выбрать из присвоеных спикеров"),
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
                tooltip=lcvt("0215|Выбрать из сохранённого списка спикеров"),
                items=[
                    ft.PopupMenuItem(
                        content=name,
                        data=name,                       
                        on_click=on_speaker_selected,  
                    )
                    for name in self.net_spknames
                ],
            )            

        btn_copy = ft.IconButton(
            icon=ft.Icons.COPY, 
            tooltip=lcvt("0216|Копировать"), 
            on_click=on_copy
        )

        btn_cancel = ft.IconButton(
            icon=ft.Icons.CLOSE, 
            icon_color=ft.Colors.RED,
            tooltip=lcvt("0217|Отмена"), 
            on_click=on_cancel
        )     
        
        btn_ok = ft.IconButton(
            icon=ft.Icons.CHECK, 
            icon_color=ft.Colors.GREEN,
            tooltip=lcvt("0218|Готово"), 
            on_click=on_ok
        )
                
        # Кнопки управления
        actions: List[ft.Control] = [
            btn_copy,
            btn_sel_int,
            btn_sel_spk,
            btn_cancel,
            btn_ok
        ]

        if not result_callback:
            actions.remove(btn_sel_int)
            actions.remove(btn_sel_spk)
            actions.remove(btn_ok)

        # Инициализация и открытие диалога
        dialog = ft.AlertDialog(
            content=ft.Container(content=text_input, width=(self.page.width or 600) * 2 / 3),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=12),
            content_padding=ft.Padding.only(left=10, right=10, top=4, bottom=4),
            actions_padding=ft.Padding.only(left=0, right=10, top=0, bottom=8),            
        )

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

@dataclass
class _LvItem:
    is_first: bool
    is_last: bool
    color: str
    spkname: str
    item: ResultSegment
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

    def if_change(self, is_first: bool, is_last: bool, color: str, spkname: str) ->None:
        if self.is_first == is_first and self.is_last == is_last and self.color == color:
            if self.spkname == spkname:
                return

            self.spkname = spkname
            if not self.is_first:
                return

        self.is_first = is_first 
        self.is_last  = is_last 
        self.color    = color

        self.refresh_row()
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

        edit_btn=ft.GestureDetector(
            content=ft.Icon(icon=ft.Icons.EDIT),
            on_tap=on_edit_click
        )        

        row.controls.clear()
        row.controls.append(text)
        row.controls.append(edit_btn)

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
