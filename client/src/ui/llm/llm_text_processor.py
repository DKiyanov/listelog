import flet as ft

from src.ui.llm.llm_models import *
from src.net.net_client import *
from src.translate import *

# ---------------------------------------------------------------------------
# Диалог обработки текста с помощью LLM
# ---------------------------------------------------------------------------
class LlmTextProcessor:
    def __init__(self, page: ft.Page, net_client: NetClient):
        self.page = page
        self.net: NetClient = net_client

        self._prompts: list[Prompt] = []
        self._connections: list[LlmConnection] = []

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # --- Текст для обработки ---
        self.tf_text = ft.TextField(
            label=lcvt("0701|Текст для обработки"),
            multiline=True,
            min_lines=15,
            expand=True,
        )

        # --- Строка управления ---
        self.dd_prompt = ft.Dropdown(
            label=lcvt("0702|Промпт:"),
            expand=True,
            on_select=self._on_prompt_selected,
        )
        self.dd_llm = ft.Dropdown(
            label=lcvt("0703|LLM:"),
            expand=True,
        )

        self.btn_run = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            icon_color=ft.Colors.GREEN,
            tooltip=lcvt("0704|Выполнить"),
            on_click=self._on_run,
        )
        self.btn_transfer = ft.IconButton(
            icon=ft.Icons.ARROW_UPWARD,
            icon_color=ft.Colors.BLUE,
            tooltip=lcvt("0705|Перенести результат в текст для обработки"),
            on_click=self._on_transfer,
            disabled=True,
        )

        self.btn_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            tooltip=lcvt("0706|Меню"),
            items=[
                ft.PopupMenuItem(content=lcvt("0707|Сохранить промпт"), on_click=self._on_save),
                ft.PopupMenuItem(content=lcvt("0708|Создать новый промпт"), on_click=self._on_create_new),
                ft.PopupMenuItem(content=lcvt("0709|Удалить промпт"), on_click=self._on_delete),
                ft.PopupMenuItem(),
                ft.PopupMenuItem(content=lcvt("0710|Создать соединение"), on_click=self._on_create_connection),
                ft.PopupMenuItem(content=lcvt("0711|Редактировать соединение"), on_click=self._on_edit_connection),
                ft.PopupMenuItem(content=lcvt("0712|Удалить соединение"), on_click=self._on_delete_connection),
            ],
        )

        controls_row = ft.Row(
            [self.dd_prompt, self.dd_llm, self.btn_run, self.btn_transfer, self.btn_menu],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Промпт ---
        self.tf_prompt = ft.TextField(
            label=lcvt("0713|Промпт"),
            multiline=True,
            min_lines=15,
            expand=True,
        )

        # --- Результат ---
        self.tf_result = ft.TextField(
            label=lcvt("0714|Результат"),
            multiline=True,
            min_lines=15,
            expand=True,
            on_change=self._on_result_change,
        )

        content = ft.Container(
            width= ((self.page.width or 0) * 10),
            expand=True,
            padding=ft.Padding.only(left=6, right=6),
            content=ft.Column(
                [self.tf_text, controls_row, self.tf_prompt, self.tf_result],
                spacing=10,
                expand=True,
            ),
        )

        def close_dlg(_e=None):
            self._dialog_close(self.dialog)

        title_row = ft.Row(expand=True, 
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color=ft.Colors.BLUE,
                    on_click=close_dlg
                ),
                ft.Text(lcvt("0715|Обработка текста с помощью LLM"), expand=True) 
            ]
        )

        self.dialog = ft.AlertDialog(
            modal=False,
            title=title_row, 
            title_padding=ft.Padding.only(bottom=4),
            content=content,
            content_padding=0, 
            inset_padding=0,
            shape=ft.RoundedRectangleBorder(radius=0),             
        )

    def _dialog_open(self, dialog: ft.DialogControl):
        self.page.show_dialog(dialog)
        
    def _dialog_close(self, dialog: ft.DialogControl):
        dialog.open =False
        self.page.update()

    # ----------------------------------------------------------- public API
    async def open(self, text: str):
        """Открывает диалог, инициализирует поля и списки."""
        self.tf_text.value = text or ""
        self.tf_prompt.value = ""
        self.tf_result.value = ""
        self.dd_prompt.value = None
        self.dd_llm.value = None

        self.is_advanced_user = "llm_admin" in await self.net.get_user_roles()

        await self._reload_prompts()
        await self._reload_connections()
        self._update_transfer_state()

        self._dialog_open(self.dialog)

    # --------------------------------------------------------------- helpers
    async def _reload_prompts(self, keep_value: bool = True):
        self._prompts = await self.net.get_prompts()
        current = self.dd_prompt.value if keep_value else None

        self.dd_prompt.options = [
            ft.dropdown.Option(key=p.title, text=p.title) for p in self._prompts
        ]
        if current and any(p.title == current for p in self._prompts):
            self.dd_prompt.value = current
        else:
            self.dd_prompt.value = None

    async def _reload_connections(self, keep_value: bool = True):
        titles = await self.net.get_connections()
        current = self.dd_llm.value if keep_value else None

        self.dd_llm.options = [ft.dropdown.Option(key=t, text=t) for t in titles]
        if current and current in titles:
            self.dd_llm.value = current
        else:
            self.dd_llm.value = None

    def _get_prompt(self, title: str | None) -> Prompt | None:
        if not title:
            return None
        for p in self._prompts:
            if p.title == title:
                return p
        return None

    def _update_transfer_state(self):
        self.btn_transfer.disabled = not (self.tf_result.value or "").strip()

    def _toast(self, msg: str, error: bool = False):
        toast = ft.SnackBar(
                content=ft.Text(msg),
                bgcolor=ft.Colors.RED_400 if error else None,
            )

        self.page.overlay.append(toast)
        toast.open = True
        self.page.update()   

    def _can_manage(self, obj) -> bool:
        """True, если текущий пользователь имеет право изменять/удалять объект."""
        if obj is None:
            return True
        if obj.is_personal:
            return True
        return self.is_advanced_user

    # -------------------------------------------------------- main handlers
    def _on_prompt_selected(self, e):
        title = self.dd_prompt.value
        if not title:
            self.tf_prompt.value = ""
            self.page.update()
            return
        p = self._get_prompt(title)
        if p:
            self.tf_prompt.value = p.text
            if p.llmcon and any(o.key == p.llmcon for o in self.dd_llm.options):
                self.dd_llm.value = p.llmcon
        self.page.update()

    def _on_result_change(self, e):
        self._update_transfer_state()
        self.page.update()

    def _on_transfer(self, e):
        self.tf_text.value = self.tf_result.value
        self.page.update()

    async def _on_run(self, e):
        text = (self.tf_text.value or "").strip()
        prompt = (self.tf_prompt.value or "").strip()
        llm = self.dd_llm.value

        if not text:
            self._toast(lcvt("0716|Введите текст для обработки"), error=True)
            return
        if not prompt:
            self._toast(lcvt("0717|Введите промпт"), error=True)
            return
        if not llm:
            self._toast(lcvt("0718|Выберите соединение LLM"), error=True)
            return

        self.btn_run.disabled = True
        self.page.update()

        try:
            result, err = await self.net.llm_processing(text, prompt, llm)
            if err:
                self._toast(err, error=True)
            else:
                self.tf_result.value = result
                self._update_transfer_state()
        finally:
            self.btn_run.disabled = False
            self.page.update()

    # ----------------------------------------------------------- Сохранить
    async def _on_save(self, e):
        title = self.dd_prompt.value

        # элемент не выбран -> создаём новый
        if not title:
            self._open_prompt_dialog()
            return

        p = self._get_prompt(title)
        # глобальный промпт, а пользователь не advanced -> создаём новый
        if p and not p.is_personal and not self.is_advanced_user:
            self._open_prompt_dialog()
            return

        raw = llm_strip_prefix(title)
        personal = p.is_personal if p else True
        err = await self.net.save_prompt(
            raw,
            self.tf_prompt.value or "",
            self.dd_llm.value or "",
            personal,
        )
        if err:
            self._toast(err, error=True)
            return

        await self._reload_prompts(keep_value=False)
        self.dd_prompt.value = llm_apply_prefix(raw, personal)
        self.page.update()

    # -------------------------------------------------- Создать новый промпт
    def _on_create_new(self, e):
        self._open_prompt_dialog()

    def _open_prompt_dialog(self):
        title_f = ft.TextField(label=lcvt("0719|Название"), autofocus=True, expand=True)
        personal_cb = ft.Checkbox(
            label=lcvt("0720|Личный промпт"),
            value=True,
            disabled=not self.is_advanced_user,
        )
        error_text = ft.Text("", color=ft.Colors.RED, visible=False)

        def close_dlg(_e=None):
            self._dialog_close(dlg)

        async def on_ok(_e):
            raw = (title_f.value or "").strip()
            if not raw:
                error_text.value = lcvt("0721|Введите название")
                error_text.visible = True
                self.page.update()
                return

            personal = personal_cb.value if personal_cb.value else False
            personal = personal if self.is_advanced_user else True

            err = await self.net.save_prompt(
                raw,
                self.tf_prompt.value or "",
                self.dd_llm.value or "",
                personal,
            )
            if err:
                error_text.value = err
                error_text.visible = True
                self.page.update()
                return

            self._dialog_close(dlg)
            await self._reload_prompts(keep_value=False)
            self.dd_prompt.value = llm_apply_prefix(raw, personal)
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(lcvt("0722|Создать новый промпт")),
            content=ft.Column(                
                [title_f, personal_cb, error_text],
                tight=True,
                spacing=10,
                width=360,
                height=120, # не нашол способа не растягивать его по высоте
            ),
            actions=[
                ft.TextButton(lcvt("0723|Отмена"), on_click=close_dlg),
                ft.TextButton(lcvt("0724|Сохранить"), on_click=on_ok),
            ],
        )
        self._dialog_open(dlg)

    # ------------------------------------------------------- Удалить промпт
    def _on_delete(self, e):
        title = self.dd_prompt.value
        if not title:
            self._toast(lcvt("0725|Промпт не выбран"), error=True)
            return

        p = self._get_prompt(title)
        if not self._can_manage(p):
            self._toast(lcvt("0726|Нет прав на удаление глобального промпта"), error=True)
            return

        def close_dlg(_e=None):
            self._dialog_close(confirm)

        async def do_delete(_e):
            await self.net.del_prompt(title)
            self._dialog_close(confirm)
            self.dd_prompt.value = None
            self.tf_prompt.value = ""
            await self._reload_prompts(keep_value=False)
            self.page.update()

        confirm = ft.AlertDialog(
            modal=True,
            title=ft.Text(lcvt("0727|Удалить промпт?")),
            content=ft.Text(f"{lcvt('0728|Удалить промпт')} «{title}»?"),
            actions=[
                ft.TextButton(lcvt("0729|Отмена"), on_click=close_dlg),
                ft.TextButton(lcvt("0730|Удалить"), on_click=do_delete),
            ],
        )
        self._dialog_open(confirm)

    # ------------------------------------------------ Создать соединение
    def _on_create_connection(self, e):
        self._open_connection_dialog()

    # ------------------------------------------ Редактировать соединение
    async def _on_edit_connection(self, e):
        title = self.dd_llm.value
        if not title:
            self._toast(lcvt("0731|Соединение не выбрано"), error=True)
            return
        conn = await self.net.get_connection(title)
        if conn is None:
            self._toast(lcvt("0732|Соединение не найдено"), error=True)
            return
        if not self._can_manage(conn):
            self._toast(lcvt("0733|Нет прав на редактирование глобального соединения"), error=True)
            return
        self._open_connection_dialog(conn)

    def _open_connection_dialog(self, existing: LlmConnection | None = None):
        title_f = ft.TextField(
            label=lcvt("0734|Название"),
            value=llm_strip_prefix(existing.title) if existing else "",
            autofocus=True,
            expand=True,
        )
        base_url_f = ft.TextField(
            label=lcvt("0735|Base URL"),
            value=existing.base_url if existing else "",
            expand=True,
        )
        api_key_f = ft.TextField(
            label=lcvt("0736|API Key"),
            value=existing.api_key if existing else "",
            password=True,
            can_reveal_password=True,
            expand=True,
        )
        model_f = ft.TextField(
            label=lcvt("0737|Модель LLM"),
            value=existing.model if existing else "",
            expand=True,
        )

        temperature_s = ft.Slider(
            min=0,
            max=100,
            value=existing.temperature * 100 if existing else 70,
            expand=True,
        )

        temperature_v = ft.Row(controls=[ft.Text(lcvt("0738|Температура")), temperature_s])

        system_f = ft.TextField(
            label=lcvt("0739|Системный промпт"),
            value=existing.system_prompt if existing else "",
            multiline=True,
            min_lines=3,
            max_lines=6,
            expand=True,
        )
        personal_cb = ft.Checkbox(
            value=existing.is_personal if existing else True,
            disabled=not self.is_advanced_user,
        )

        personal_v = ft.Row(controls=[ft.Text(lcvt("0740|Личное соединение")), personal_cb])

        error_text = ft.Text("", color=ft.Colors.RED, visible=False)

        def close_dlg(_e=None):
            self._dialog_close(dlg)

        async def on_ok(_e):
            raw = (title_f.value or "").strip()
            if not raw:
                error_text.value = lcvt("0741|Введите название")
                error_text.visible = True
                self.page.update()
                return

            personal = personal_cb.value if personal_cb.value else False
            personal = personal if self.is_advanced_user else True

            temperature = temperature_s.value or 0
            
            err = await self.net.save_connection(
                raw,
                base_url_f.value or "",
                api_key_f.value or "",
                model_f.value or "",
                system_f.value or "",
                temperature / 100,
                personal,
            )
            if err:
                error_text.value = err
                error_text.visible = True
                self.page.update()
                return

            self._dialog_close(dlg)
            await self._reload_connections(keep_value=False)
            self.dd_llm.value = llm_apply_prefix(raw, personal)
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                lcvt("0742|Редактировать соединение") if existing else lcvt("0743|Создать соединение")
            ),
            content=ft.Column(
                [
                    title_f, 
                    base_url_f, 
                    api_key_f, 
                    model_f, 
                    system_f, 
                    temperature_v, 
                    personal_v, 
                    error_text
                ],
                tight=True,
                spacing=10,
                width=460,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton(lcvt("0744|Отмена"), on_click=close_dlg),
                ft.TextButton(lcvt("0745|Сохранить"), on_click=on_ok),
            ],
        )
        self._dialog_open(dlg)

    # ---------------------------------------------- Удалить соединение
    async def _on_delete_connection(self, e):
        title = self.dd_llm.value
        if not title:
            self._toast(lcvt("0746|Соединение не выбрано"), error=True)
            return

        conn = await self.net.get_connection(title)
        if not self._can_manage(conn):
            self._toast(lcvt("0747|Нет прав на удаление глобального соединения"), error=True)
            return

        def close_dlg(_e=None):
            self._dialog_close(confirm)

        async def do_delete(_e):
            await self.net.del_connection(title)
            self._dialog_close(confirm)
            self.dd_llm.value = None
            await self._reload_connections(keep_value=False)
            self.page.update()

        confirm = ft.AlertDialog(
            modal=True,
            title=ft.Text(lcvt("0748|Удалить соединение?")),
            content=ft.Text(f"{lcvt('0749|Удалить соединение')} «{title}»?"),
            actions=[
                ft.TextButton(lcvt("0750|Отмена"), on_click=close_dlg),
                ft.TextButton(lcvt("0751|Удалить"), on_click=do_delete),
            ],
        )
        self._dialog_open(confirm)    
