import sys
import os
import flet as ft
from src.translate import lcvt

def is_compiled() ->bool:
    return hasattr(sys, '_MEIPASS')

def get_work_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)

def show_toast(page: ft.Page, message: str):
    # Создаем компактный SnackBar в стиле Toast
    toast = ft.SnackBar(
        content=ft.Text(message, text_align=ft.TextAlign.CENTER),
        width=200,
        duration=2000, 
    )
    page.overlay.append(toast)
    toast.open = True
    page.update()        

def show_confirm_dialog(page: ft.Page, title: str, text: str, on_yes, on_no=None):
    """
    Универсальный метод для вывода диалога подтверждения.
    :param page: Ссылка на текущую страницу ft.Page
    :param title: Заголовок окна
    :param text: Текст вопроса/сообщения
    :param on_yes: Функция, выполняемая при нажатии "Да"
    :param on_no: Функция, выполняемая при нажатии "Нет" (необязательно)
    """
    def close_dialog(e):
        dialog.open = False
        page.update()

    def yes_click(e):
        close_dialog(e)
        if on_yes:
            on_yes()

    def no_click(e):
        close_dialog(e)
        if on_no:
            on_no()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(text),
        actions=[
            ft.TextButton(lcvt("0601|Да"), on_click=yes_click),
            ft.TextButton(lcvt("0602|Нет"), on_click=no_click),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()    