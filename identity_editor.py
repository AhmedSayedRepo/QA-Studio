"""Shared draggable editor for profile photos and organization logos.

The editor intentionally works on an already-validated square image.  It
keeps panning inside the available source pixels, so the circular avatar and
rounded logo never gain empty corners after drag/zoom/rotation edits.
"""
import base64
import time

import flet as ft
import requests

import image_assets
import theme as T


def _payload_source(payload):
    return "data:{mime};base64,{body}".format(
        mime=payload["mime_type"], body=payload["image_base64"])


def _load_source(source):
    source = str(source or "")
    if source.startswith("data:image/") and "," in source:
        header, body = source.split(",", 1)
        return base64.b64decode(body), header.split(";", 1)[0].replace("data:", "")
    response = requests.get(source, timeout=15)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type", "")


def open_editor(app, *, source, title, choose_label, save_label, close_label,
                reset_label, remove_tooltip, loading_label, failed_label,
                drag_hint, zoom_label, rotate_label, uploading_label,
                on_choose_error, on_save, on_remove=None, positioned_label="",
                load_failed_label=""):
    """Open the common photo/logo editor.

    ``on_save(payload)`` and optional ``on_remove()`` run off the UI thread and
    return ``(ok, result_or_error)``.  The callbacks are responsible for their
    own domain-specific persistence; this component owns only image editing.
    """
    state = {"source": None, "zoom": 1.0, "rotation": 0, "x": 0.0, "y": 0.0,
             "drag_start": None, "drag_xy": (0.0, 0.0), "last_draw": 0.0}
    spinner = ft.ProgressRing(width=28, height=28, stroke_width=3, color=T.VIOLET)
    loading = ft.Container(
        ft.Column([spinner, ft.Text(loading_label, color=T.INK_2)],
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                  alignment=ft.MainAxisAlignment.CENTER, spacing=14),
        width=360, height=360, alignment=ft.Alignment.CENTER)
    dialog = ft.AlertDialog(
        modal=False, title=ft.Text(title, size=15, weight=ft.FontWeight.W_800, color=T.INK),
        content=loading, actions=[ft.TextButton(close_label, on_click=lambda e: app._close_all_dialogs())],
        bgcolor=T.CARD)
    app._show_dialog(dialog)

    def prepare():
        if source:
            try:
                raw, _mime = _load_source(source)
                payload, reason = image_assets.validate_square_image(raw)
                if payload is None:
                    raise ValueError(reason or "invalid image")
                state["source"] = payload
            except Exception:
                # The header may still render a previously fetched image while
                # its short-lived private Storage URL has expired. Opening the
                # editor must not become a dead end in that case: leave the
                # source empty and let the user choose a replacement.
                state["source_error"] = True

        def preview_payload(max_pixels=512):
            if not state["source"]:
                return None, "empty"
            return image_assets.transform_validated_square_image(
                state["source"], state["zoom"], state["rotation"],
                state["x"], state["y"], preview_max_pixels=max_pixels)

        initial, _reason = preview_payload()
        preview_image = ft.Image(src=_payload_source(initial), width=300, height=300,
                                 fit=ft.BoxFit.COVER, border_radius=150) if initial else None
        preview_holder = ft.Container(
            preview_image if preview_image else ft.Icon(ft.Icons.ADD_A_PHOTO_OUTLINED, size=48, color=T.VIOLET_INK),
            width=300, height=300, alignment=ft.Alignment.CENTER)
        zoom_text = ft.Text("100%", size=12, color=T.INK_2, width=48, text_align=ft.TextAlign.CENTER)
        rotation_text = ft.Text("0°", size=12, color=T.INK_2, width=48, text_align=ft.TextAlign.CENTER)
        position_text = ft.Text((load_failed_label if state.get("source_error") else drag_hint),
                                size=10.5, color=T.INK_3, width=284,
                                text_align=ft.TextAlign.CENTER, no_wrap=False)

        def refresh_preview(max_pixels=512):
            payload, reason = preview_payload(max_pixels)
            if payload is None:
                if reason != "empty":
                    app._err(failed_label)
                return
            if isinstance(preview_holder.content, ft.Image):
                preview_holder.content.src = _payload_source(payload)
                preview_holder.content.update()
            else:
                preview_holder.content = ft.Image(src=_payload_source(payload), width=300, height=300,
                                                  fit=ft.BoxFit.COVER, border_radius=150)
                preview_holder.update()
            zoom_text.value = "{value:.0f}%".format(value=state["zoom"] * 100)
            rotation_text.value = "{value}°".format(value=state["rotation"])
            position_text.value = (load_failed_label if state.get("source_error") else
                                   (drag_hint if (abs(state["x"]) < .01 and abs(state["y"]) < .01)
                                    else drag_hint + " • " + positioned_label))
            zoom_text.update(); rotation_text.update(); position_text.update()

        def change_zoom(delta):
            state["zoom"] = max(0.5, min(2.0, round(state["zoom"] + delta, 1)))
            if state["zoom"] <= 1.0:
                state["x"] = state["y"] = 0.0
            refresh_preview()

        def change_rotation(delta):
            state["rotation"] = ((state["rotation"] + delta + 180) % 360) - 180
            rotation_slider.value = state["rotation"]
            rotation_slider.update()
            refresh_preview()

        def preview_rotation(e):
            try:
                state["rotation"] = int(round(float(e.control.value)))
            except (TypeError, ValueError):
                return
            rotation_text.value = "{value}°".format(value=state["rotation"])
            rotation_text.update()

        def commit_rotation(e):
            preview_rotation(e)
            refresh_preview()

        def pan_start(e):
            if state["zoom"] <= 1.0:
                return
            point = getattr(e, "global_position", None)
            state["drag_start"] = (float(getattr(point, "x", 0) or 0), float(getattr(point, "y", 0) or 0))
            state["drag_xy"] = (state["x"], state["y"])

        def pan_update(e):
            if state["zoom"] <= 1.0 or not state["drag_start"]:
                return
            point = getattr(e, "global_position", None)
            px, py = float(getattr(point, "x", 0) or 0), float(getattr(point, "y", 0) or 0)
            sx, sy = state["drag_start"]
            ox, oy = state["drag_xy"]
            # Moving the pointer right exposes the photo's left side, hence the
            # intentionally opposite crop direction.  140 px reaches an edge.
            state["x"] = max(-1.0, min(1.0, ox - ((px - sx) / 140.0)))
            state["y"] = max(-1.0, min(1.0, oy - ((py - sy) / 140.0)))
            now = time.monotonic()
            if now - state["last_draw"] >= 0.12:
                state["last_draw"] = now
                refresh_preview(256)

        def pan_end(e):
            state["drag_start"] = None
            refresh_preview()

        def reset_edit(e=None):
            state.update({"zoom": 1.0, "rotation": 0, "x": 0.0, "y": 0.0})
            rotation_slider.value = 0
            rotation_slider.update()
            refresh_preview()

        def choose_image(e=None):
            def selected(payload):
                state.update({"source": payload, "zoom": 1.0, "rotation": 0, "x": 0.0, "y": 0.0})
                state["source_error"] = False
                rotation_slider.value = 0
                rotation_slider.update()
                remove_button.visible = callable(on_remove)
                remove_button.update()
                refresh_preview()
            image_assets.choose_square_image(app, choose_label, selected, on_choose_error)

        def save_edit(e=None):
            payload, reason = image_assets.transform_validated_square_image(
                state["source"], state["zoom"], state["rotation"], state["x"], state["y"])
            if payload is None:
                app._err(failed_label)
                return
            app._toast(uploading_label)
            def work():
                try:
                    ok, result = on_save(payload)
                except Exception as ex:
                    ok, result = False, str(ex)
                if not ok:
                    app.ui_safe(lambda: app._err(str(result)[:160]))
            app._bg(work)

        def remove_image(e=None):
            if not callable(on_remove):
                return
            app._toast(uploading_label)
            def work():
                try:
                    ok, result = on_remove()
                except Exception as ex:
                    ok, result = False, str(ex)
                if not ok:
                    app.ui_safe(lambda: app._err(str(result)[:160]))
            app._bg(work)

        rotation_slider = ft.Slider(min=-180, max=180, divisions=360, value=0, width=250,
                                    label="{value}°", active_color=T.VIOLET,
                                    inactive_color=T.BORDER, thumb_color=T.VIOLET,
                                    on_change=preview_rotation, on_change_end=commit_rotation)
        preview_circle = ft.Container(
            ft.GestureDetector(content=preview_holder, on_pan_start=pan_start,
                               on_pan_update=pan_update, on_pan_end=pan_end,
                               drag_interval=120, mouse_cursor=ft.MouseCursor.MOVE),
            width=308, height=308, padding=4, border_radius=154, bgcolor=T.CARD_2,
            clip_behavior=ft.ClipBehavior.HARD_EDGE)
        remove_button = ft.Container(
            ft.Icon(ft.Icons.CLOSE, size=15, color="#FFFFFF"), width=28, height=28,
            border_radius=14, bgcolor=T.RED, alignment=ft.Alignment.CENTER,
            on_click=remove_image, ink=True, tooltip=remove_tooltip,
            visible=(bool(state["source"]) or state.get("source_error")) and callable(on_remove), right=0, top=0)
        preview_frame = ft.Stack([
            ft.Container(preview_circle, left=6, top=6),
            remove_button,
        ], width=322, height=322, clip_behavior=ft.ClipBehavior.NONE)

        def button(label, icon, handler):
            return ft.TextButton(label, icon=icon, on_click=handler)

        controls = ft.Column([
            ft.Row([ft.Text(zoom_label, weight=ft.FontWeight.W_700, color=T.INK),
                    button("−", ft.Icons.ZOOM_OUT, lambda e: change_zoom(-0.1)), zoom_text,
                    button("+", ft.Icons.ZOOM_IN, lambda e: change_zoom(0.1))],
                   alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([ft.Text(rotate_label, weight=ft.FontWeight.W_700, color=T.INK),
                    button("−90°", ft.Icons.ROTATE_LEFT, lambda e: change_rotation(-90)), rotation_text,
                    button("+90°", ft.Icons.ROTATE_RIGHT, lambda e: change_rotation(90))],
                   alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(rotation_slider, padding=ft.Padding.symmetric(horizontal=8), width=284),
            position_text,
        ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        editor = ft.Column([preview_frame, controls], spacing=14,
                           horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True)

        def apply_editor():
            dialog.content = editor
            dialog.actions = [
                ft.TextButton(choose_label, icon=ft.Icons.UPLOAD, on_click=choose_image),
                ft.TextButton(reset_label, on_click=reset_edit),
                ft.TextButton(close_label, on_click=lambda e: app._close_all_dialogs()),
                ft.FilledButton(save_label, icon=ft.Icons.SAVE, on_click=save_edit),
            ]
            dialog.update()
        app.ui_safe(apply_editor)

    app._bg(prepare)
