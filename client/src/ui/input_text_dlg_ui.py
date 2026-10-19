import asyncio
import flet as ft

from src.translate import lcvt

async def input_text_dlg(page: ft.Page, title: str, label: str, hint:str, text:str ) -> str | None:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    input_text: ft.TextField = ft.TextField(
        label=label,
        hint_text= hint
    )

    content = ft.Column(
        controls=[
            input_text
        ],
        spacing=0,
        tight=True,
    )

    def _handle_cancel(self) -> None:
        if not future.done():
            future.set_result(None)        
        dialog.open =False
        page.update()

    def _handle_ok(self) -> None:
        if not future.done():
            future.set_result(input_text.value)
        dialog.open =False
        page.update()
  
    # Настраиваем диалоговое окно
    dialog = ft.AlertDialog(
        title=ft.Text(title),
        content=content,
        actions=[
            ft.Button(lcvt("0901|Отмена"), icon=ft.Icons.CANCEL, on_click=_handle_cancel),
            ft.Button(lcvt("0902|Ок"), icon=ft.Icons.CHECK, on_click=_handle_ok),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # Добавляем диалог на страницу и открываем его
    page.overlay.append(dialog)
    dialog.open = True
    page.update()

    return await future
