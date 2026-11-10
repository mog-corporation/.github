#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""valida_ticket_issue.py - puerta fail-closed del formulario Ticket de GitHub Issues (fuente unica de la org).

Un issue es REGISTRO DE TRABAJO, no canal de mensajes: para entrar necesita los seis campos obligatorios
declarados en `.github/scripts/issue-taxonomy.active.json` (Writer, verificador independiente, criterio de
cierre EJECUTABLE, rutas afectadas, repo destino y JOB). La lista de campos, sus titulos y los prefijos de
comando aceptados salen del registro; aqui no hay vocabulario horneado.

Este fichero vive en mog-corporation/.github y lo invoca el workflow reutilizable `valida-ticket.yml` desde el
SHA que fija cada caller: no se copia a los repos destino (una sola copia, ningun vocabulario divergente).

Contrato de E/S:
  valida_ticket_issue.py --cuerpo FICHERO --taxonomia FICHERO [--json]
  exit 0 = ACEPTADO; exit 1 = RECHAZADO (falta algo, y se dice que); exit 2 = NO_MEDIDO (registro ilegible).

Fail-closed: cualquier fallo interno (registro ausente, YAML/JSON roto, excepcion al parsear el cuerpo)
devuelve NO_MEDIDO y NUNCA ACEPTADO. Un issue con el cuerpo vacio no es un ticket valido: se rechaza con
los seis campos como faltantes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SIN_RESPUESTA = "_no response_"
TITULO_RE = re.compile(r"^###\s+(.+?)\s*$", re.M)
# Ultima marca conocida si el registro no se puede leer. Sirve para SENALAR el rechazo, nunca para autorizar
# nada: con el registro roto el veredicto es NO_MEDIDO y el issue queda marcado igual.
ETIQUETA_DE_EMERGENCIA = "necesita-plantilla"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
JOB_RE = re.compile(r"^j[0-9a-f]{12}$", re.I)
RUTA_RE = re.compile(r"(?:^|[\s`])(?:[\w.-]+/[\w./-]+|[\w.-]+\.(?:py|ya?ml|json|md|sh|ts|js|mjs|gs|txt))")


class NoMedido(Exception):
    """El registro de taxonomia no se puede leer: no se autoriza nada."""


def carga_taxonomia(ruta: Path) -> dict:
    """Lee el registro gobernado. Cualquier anomalia es NO_MEDIDO (fail-closed)."""
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoMedido("TAXONOMIA_ILEGIBLE %s: %s" % (ruta, exc)) from exc
    if not isinstance(d, dict) or d.get("schema") != "mog.issue-taxonomy.v1":
        raise NoMedido("TAXONOMIA_SCHEMA_INESPERADO %s" % (d.get("schema") if isinstance(d, dict) else type(d).__name__))
    requeridos = d.get("requeridos")
    formulario = d.get("formulario")
    if not isinstance(requeridos, list) or not requeridos or not isinstance(formulario, dict):
        raise NoMedido("TAXONOMIA_SIN_REQUERIDOS_O_FORMULARIO")
    faltan_etiqueta = [k for k in requeridos if not isinstance(formulario.get(k), str) or not formulario.get(k)]
    if faltan_etiqueta:
        raise NoMedido("TAXONOMIA_REQUERIDO_SIN_TITULO %s" % ",".join(faltan_etiqueta))
    return d


def campos(cuerpo: str) -> dict:
    """Seccion del Issue Form -> valor. Los formularios renderizan `### <label>` y un bloque por campo."""
    secciones: dict[str, str] = {}
    marcas = list(TITULO_RE.finditer(cuerpo or ""))
    for i, m in enumerate(marcas):
        titulo = m.group(1).strip()
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(cuerpo)
        secciones[titulo] = (cuerpo[m.end():fin] or "").strip()
    return secciones


def _limpia(valor: str) -> str:
    """Quita comillas, vallas y el marcador del formulario; deja el contenido util del campo.

    El orden importa: un `_No response_` envuelto en backticks (alguien lo pego a mano) tiene que seguir
    contando como campo vacio, no como texto valido."""
    v = (valor or "").strip().strip("`").strip()
    if v.lower() == SIN_RESPUESTA:
        return ""
    v = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", v)
    v = re.sub(r"\n?```\s*$", "", v)
    v = v.strip().strip("`").strip()
    return "" if v.lower() == SIN_RESPUESTA else v


def criterio_ejecutable(valor: str, prefijos: list[str]) -> bool:
    """True si el criterio de cierre trae al menos una linea que sea un comando lanzable."""
    if not valor or not prefijos:
        return False
    patron = re.compile(r"^(?:%s)(?:\s|$)" % "|".join(re.escape(p) for p in prefijos), re.I)
    for linea in valor.splitlines():
        limpia = linea.strip().lstrip("$").strip()
        if patron.match(limpia):
            return True
    return False


def valida(cuerpo: str, tax: dict) -> dict:
    """(veredicto, causas). Funcion pura sobre el cuerpo y el registro."""
    prefijos = tax.get("criterio_prefijos") or []
    vistos = campos(cuerpo)
    causas: list[str] = []
    for clave in tax["requeridos"]:
        titulo = tax["formulario"][clave]
        valor = _limpia(vistos.get(titulo, ""))
        if not valor:
            causas.append("FALTA_" + clave.upper())
            continue
        if clave == "criterio" and not criterio_ejecutable(valor, prefijos):
            causas.append("CRITERIO_NO_EJECUTABLE")
        elif clave == "repo_destino" and not REPO_RE.match(valor.split()[0]):
            causas.append("REPO_DESTINO_INVALIDO")
        elif clave == "job" and not JOB_RE.match(valor.split()[0]):
            causas.append("JOB_INVALIDO")
        elif clave == "rutas" and not RUTA_RE.search(valor):
            causas.append("RUTAS_NO_RECONOCIBLES")
    return {"veredicto": "RECHAZADO" if causas else "ACEPTADO", "causas": causas}


def cuerpo_rechazo(causas: list[str], etiqueta: str) -> str:
    """Comentario de rechazo. Funcion pura: mismo veredicto -> mismo texto, sin fecha ni contadores.

    Vive aqui (y no incrustado en el YAML) para que el banco pueda medir su determinismo: si dos corridas
    sobre el mismo cuerpo producen comentarios distintos, el issue se llena de ruido y deja de ser registro.
    """
    return (
        "## Gate de tickets: rechazado (fail-closed)\n\n"
        "Este issue no cumple el formulario `Ticket`. Falta o no es valido:\n\n"
        + "\n".join("- `%s`" % c for c in causas)
        + "\n\nCorrige el cuerpo del issue: al editarlo la puerta vuelve a medir, y la etiqueta `"
        + etiqueta
        + "` se retira sola cuando los seis campos estan completos.\n\n"
        "Avisos de operacion van a Discussions; decisiones van a acta. Este issue no es canal de mensajes.\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Puerta del formulario Ticket (fail-closed)")
    ap.add_argument("--cuerpo", required=True)
    ap.add_argument("--taxonomia", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    etiqueta = ETIQUETA_DE_EMERGENCIA
    try:
        tax = carga_taxonomia(Path(a.taxonomia))
        etiqueta = (tax.get("gate") or {}).get("etiqueta_rechazo") or etiqueta
        cuerpo = Path(a.cuerpo).read_text(encoding="utf-8", errors="replace")
        r = valida(cuerpo, tax)
    except NoMedido as exc:
        r = {"veredicto": "NO_MEDIDO", "causas": [str(exc)]}
    except Exception as exc:  # fail-closed: una excepcion no autoriza nada
        r = {"veredicto": "NO_MEDIDO", "causas": ["ERROR_DE_INSTRUMENTO %s" % exc]}
    r["etiqueta_rechazo"] = etiqueta
    if a.json:
        print(json.dumps(r, ensure_ascii=False, sort_keys=True))
    else:
        print("%s %s" % (r["veredicto"], ", ".join(r["causas"]) or "-"))
    return 0 if r["veredicto"] == "ACEPTADO" else (2 if r["veredicto"] == "NO_MEDIDO" else 1)


if __name__ == "__main__":
    sys.exit(main())
