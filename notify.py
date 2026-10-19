"""Envío de notificaciones por mail (SMTP) al responsable de validación."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from typing import Mapping, Optional


def smtp_configured(cfg: Optional[Mapping]) -> bool:
    return bool(cfg and cfg.get("host") and cfg.get("user") and cfg.get("password"))


def build_message(cfg: Mapping, task: Mapping, app_url: str = "") -> EmailMessage:
    to = task["responsable_validacion"]
    fecha = task.get("fecha_limite")
    try:
        fecha_txt = fecha.strftime("%d/%m/%Y") if fecha else "Sin fecha"
    except (AttributeError, ValueError):  # NaT / NaN
        fecha_txt = "Sin fecha"
    ejecuta = task.get("responsable_ejecucion") or "Sin asignar"
    link = task.get("link_formulario") or ""

    filas = [
        ("Tarea", task["tarea"]),
        ("Responsable de ejecución", ejecuta),
        ("Prioridad", task.get("prioridad", "")),
        ("Estado", task.get("estado", "")),
        ("Fecha límite", fecha_txt),
    ]

    texto = [
        "Hola,",
        "",
        "Se te asignó como responsable de VALIDACIÓN de una tarea en el Tablero de Seguimiento:",
        "",
        *[f"- {k}: {v}" for k, v in filas],
    ]
    if link:
        texto += ["", f"Formulario de la tarea: {link}"]
    if app_url:
        texto += [f"Tablero: {app_url}"]
    texto += ["", "Este es un mensaje automático."]

    filas_html = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td>"
        f"<td style='padding:4px 0'><b>{escape(str(v))}</b></td></tr>"
        for k, v in filas
    )
    botones = ""
    if link:
        botones += (
            f"<a href='{escape(link)}' style='display:inline-block;background:#1a73e8;color:#fff;"
            "padding:10px 16px;border-radius:6px;text-decoration:none;margin-right:8px'>"
            "Abrir formulario</a>"
        )
    if app_url:
        botones += (
            f"<a href='{escape(app_url)}' style='display:inline-block;background:#eee;color:#222;"
            "padding:10px 16px;border-radius:6px;text-decoration:none'>Ir al tablero</a>"
        )
    html = f"""\
<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
  <p>Hola,</p>
  <p>Se te asignó como <b>responsable de validación</b> de una tarea en el Tablero de Seguimiento:</p>
  <table>{filas_html}</table>
  <p style="margin-top:16px">{botones}</p>
  <p style="color:#888;font-size:12px">Este es un mensaje automático.</p>
</div>"""

    msg = EmailMessage()
    msg["Subject"] = f"[Tablero] Validación asignada: {task['tarea']}"
    sender = cfg.get("from_email") or cfg["user"]
    name = cfg.get("from_name") or "Tablero de Seguimiento"
    msg["From"] = f"{name} <{sender}>"
    msg["To"] = to
    msg.set_content("\n".join(texto))
    msg.add_alternative(html, subtype="html")
    return msg


def send_validation_assignment(cfg: Mapping, task: Mapping, app_url: str = "") -> None:
    """Envía el mail. Lanza excepción si falla (la app la muestra como aviso)."""
    msg = build_message(cfg, task, app_url)
    host = cfg["host"]
    port = int(cfg.get("port", 587))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
