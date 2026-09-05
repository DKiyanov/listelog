from typing import List, Callable
import flet as ft
from datetime import datetime
import locale

from src.models import *
from src.translate import lcvt

def select_stt_session(page: ft.Page, ses_list: List[SttSession], on_select: OnSelectSttSession):
    locale.setlocale(locale.LC_TIME, "")
    date_time_width: int = 150

    def conv_dt(dtin: str) -> str:
        dt_object = datetime.strptime(dtin, "%Y%m%d%H%M")
        nval = f"{dt_object.strftime('%x')} {dt_object.strftime('%X')[0:5]}"
        return nval

    # Обработчик выбора строки
    def on_row_select(session: SttSession):
        dialog.open = False
        page.update()
        on_select(session)

    
    header_row = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(lcvt("0501|Дата и время"), weight=ft.FontWeight.BOLD, width=date_time_width),
                ft.Text(lcvt("0502|Наименование"), weight=ft.FontWeight.BOLD, expand=True),
            ],
            alignment=ft.MainAxisAlignment.START,
        ),
        padding=ft.Padding.only(left=24, right=24, top=12, bottom=12),
        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
    )

    def create_clickable_row(ses: SttSession):
        return ft.GestureDetector(
            on_tap=lambda e: on_row_select(ses),
            mouse_cursor=ft.MouseCursor.CLICK,
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Text(conv_dt(ses.date), width=date_time_width, max_lines=1, overflow=ft.TextOverflow.CLIP),
                        ft.Text(ses.title, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ],
                ),
                padding=ft.Padding.symmetric(vertical=12, horizontal=24),
                border=ft.Border.only(bottom=ft.BorderSide(0.5, ft.Colors.OUTLINE_VARIANT)),
            )
        )

    # Контейнер со списком строк и фиксированным заголовком
    content_layout = ft.Column(
        controls=[
            header_row,
            ft.Container(
                content=ft.ListView(
                    controls=[create_clickable_row(ses) for ses in ses_list],
                    spacing=0,
                ),
                expand=True
            )
        ],
        spacing=0,
        tight=True,
    )


    if page.window.width is None: return

    content_container = ft.Container(
        content= content_layout,
        width= page.window.width // 2
    )

    # Настраиваем диалоговое окно
    dialog = ft.AlertDialog(
        title=ft.Text(lcvt("0503|Выберите запись")),
        content=content_container,
        actions=[
            ft.TextButton(lcvt("0504|Отмена"), on_click=lambda e: setattr(dialog, "open", False) or page.update())
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # Добавляем диалог на страницу и открываем его
    page.overlay.append(dialog)
    dialog.open = True
    page.update()
