import flet as ft
import asyncio
from enum import StrEnum
from dataclasses import asdict
import json

from src.models import *
from src.translate import lcvt

spk_symbol = "🧑"

class TextFormat(StrEnum):
    subtitle  = "srt"
    text      = "txt"
    table_csv = "csv"
    table_tsv = "tsv"
    json      = "json"

def get_subtitle_time_str(pos: int) -> str:
    total_seconds = pos // 1000
    milliseconds = pos % 1000
    
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    # Собираем строку с ведущими нулями
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"    

def result_get_text(text_format: TextFormat, result_list: List[ResultSegment], spknmap: dict[str, str])->str:
    text: str = ""
    spk_name: str = "-"

    if text_format == TextFormat.text:
        for item in result_list:
            item_spk_name = spknmap[item.spkid]

            if spk_name != item_spk_name:
                spk_name = item_spk_name
                text += f"\n {spk_symbol} {spk_name}"

            text += f"\n{item.text}"
    
    if text_format == TextFormat.subtitle:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index}"
            text += f"\n{get_subtitle_time_str(item.tss)} --> {get_subtitle_time_str(item.tse)}"

            item_spk_name = spknmap[item.spkid].replace(" ", "_")

            if spk_name != item_spk_name:
                spk_name = item_spk_name
                text += f"\n{spk_symbol}{spk_name} {item.text}"
            else:
                text += f"\n{item.text}"

            text += f"\n"

    if text_format == TextFormat.table_csv:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index},{item.spkid},{item.tss},{item.tse},{item.text.replace(',', ' ')}"

    if text_format == TextFormat.table_tsv:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index}\t{item.spkid}\t{item.tss}\t{item.tse}\t{item.text}"
            
    if text_format == TextFormat.json:
        dicts = [asdict(result) for result in result_list]
        text = json.dumps(dicts, indent=4, ensure_ascii=False)

    return text

def result_save_dlg(
    page: ft.Page, 
    result_list: List[ResultSegment], 
    spknmap: dict[str, str],
    save_file_path: str = ""
):
    text_format: TextFormat = TextFormat.text
    text = result_get_text(text_format, result_list, spknmap)

    selection = {"start": -1, "end": -1}

    def on_selection_change(e: ft.TextSelectionChangeEvent[ft.TextField]) -> None:
        if e.control.selection is None: return  
        selection["start"] = e.control.selection.start 
        selection["end"] = e.control.selection.end    

    # Создаем многострочное поле ввода
    main_text = ft.TextField(
        value=text,
        read_only=True,
        multiline=True,
        expand=True,
        autofocus=True,
        border=ft.InputBorder.NONE,
        on_selection_change=on_selection_change
    )

    main_text.on_selection_change

    def close_dialog():
        dialog.open = False
        page.update()

    def on_cancel(e):
        close_dialog()

    def show_message(msg:str):
        msgdlg = ft.AlertDialog(
            content=ft.Text(msg),
            actions_padding=ft.Padding.zero()
        )

        page.overlay.append(msgdlg)
        msgdlg.open = True
        page.update()        

        asyncio.create_task(close_message(msgdlg))

    async def close_message(msgdlg: ft.AlertDialog):
        await asyncio.sleep(2)
        msgdlg.open = False
        page.update()

    def on_save(e):
        if not file_path_input.value:
            show_message(lcvt("0901|Укажите путь и имя файла для сохранения"))
            return

        prepare_file_extension()

        save_err = ""
        try:
            with open(file_path_input.value, 'w', encoding='utf-8') as file:
                file.write(text)  
        except PermissionError:
            save_err = lcvt("0902|Ошибка: Нет прав для записи в файл")
        except FileNotFoundError:
            save_err = lcvt(f"0903|Ошибка: Указана несуществующая папка в пути к файлу")
        except OSError as e:
            save_err = lcvt(f"0904|Системная ошибка устройства")
        except Exception as e:
            save_err = lcvt(f"0905|Произошла непредвиденная ошибка")
        
        if save_err:
            show_message(save_err)
            return

        show_message(lcvt("0906|Файл успешно сохранён"))

    def on_copy(e):
        full_text = main_text.value or ""
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

    btn_copy = ft.IconButton(
        icon=ft.Icons.COPY, 
        tooltip=lcvt("0907|Копировать"), 
        on_click=on_copy
    )

    btn_cancel = ft.IconButton(
        icon=ft.Icons.CLOSE, 
        icon_color=ft.Colors.RED,
        tooltip=lcvt("0908|Отмена"), 
        on_click=on_cancel
    )     
    
    btn_save = ft.IconButton(
        icon=ft.Icons.SAVE, 
        icon_color=ft.Colors.BROWN,
        tooltip=lcvt("0909|Сохранить"), 
        on_click=on_save
    )

    async def on_browse_click(e):
        # Открываем диалог выбора файлов 
        file_picker = ft.FilePicker()
        result = await file_picker.save_file(
            dialog_title=lcvt("0910|Выберите место для сохранения файла"),
            file_name=file_path_input.value,
            allowed_extensions=["srt", "txt", "csv", "json"]
        )
        if result:      
            file_path_input.value = result
        else:
            file_path_input.value = ""
        file_path_input.update()

    def on_text_format_selected(e):
        text_format = TextFormat(text_format_ddn.value)
        main_text.value = result_get_text(text_format, result_list, spknmap)
        main_text.update()
        prepare_file_extension()
        file_path_input.update()

    def prepare_file_extension():
        if not file_path_input.value: return
        file_path_input.value = str(Path(file_path_input.value).with_suffix(f".{text_format.value}"))

    ddn_options = [ft.dropdown.Option(key=tf, text=lcvt(f"text_format_{tf.value}")) for tf in TextFormat]
    
    text_format_ddn = ft.Dropdown(
        label=lcvt("0911|Формат"),
        value= text_format,
        on_select=on_text_format_selected,
        options=ddn_options,
    )

    file_path_input = ft.TextField(
        hint_text=lcvt("0912|Сохранить в файл"),
        value=save_file_path,
        expand=True,
        suffix_icon=ft.GestureDetector(
            content=ft.Icon(
                icon=ft.Icons.FOLDER_ROUNDED,
                color=ft.Colors.YELLOW,
                tooltip=lcvt("0913|Выбор файла для сохранения")
            ),
            on_tap=on_browse_click
        )
    )    

    prepare_file_extension()
            
    # Кнопки управления
    actions_row = ft.Row(
        alignment=ft.MainAxisAlignment.END,
        controls=[
            text_format_ddn,
            file_path_input,
            btn_save,
            btn_copy,
            btn_cancel,        
        ]
    )

    # Инициализация и открытие диалога
    dialog = ft.AlertDialog(
        content=ft.Container(content=main_text, width=(page.width or 600) * 2 / 3),
        actions=[actions_row],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
        content_padding=ft.Padding.only(left=10, right=10, top=4, bottom=4),
        actions_padding=ft.Padding.only(left=10, right=10, top=0, bottom=8),            
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()