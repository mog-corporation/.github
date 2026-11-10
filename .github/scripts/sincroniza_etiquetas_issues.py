#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sincroniza_etiquetas_issues.py - vocabulario de etiquetas de issues desde el registro gobernado.

El vocabulario NO se hornea en el workflow ni en este script: sale de `.github/scripts/issue-taxonomy.active.json`
(areas, prioridad, estado y la etiqueta de rechazo del gate). Este script es la fuente unica de la org: se corre
desde aqui contra cada repo (`--repo OWNER/REPO`); no se copia a los repos destino.

Contrato de E/S:
  sincroniza_etiquetas_issues.py --repo OWNER/REPO [--taxonomia F] [--aplica] [--aditiva] [--json]
  Sin --aplica: mide la deriva (crear/actualizar) y sale 1 si la hay. Con --aplica: crea/actualiza con
  `gh label create --force` y vuelve a medir. exit 2 = NO_MEDIDO (registro ilegible o gh caido).

`--aditiva`: no aplica `retiradas` del registro. Es el modo de la fan-out: crea el vocabulario y no retira nada,
porque retirar una etiqueta la quitaria de los issues ya etiquetados y aqui no se toca ningun issue existente.

No borra etiquetas ni toca issues: este script no tiene ninguna ruta de borrado. Reetiquetar el backlog es otro
encargo, con su dictamen.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PREFIJO_A_SECCION = (("area", "areas"), ("prioridad", "prioridad"), ("estado", "estado"))


class NoMedido(Exception):
    pass


def carga(ruta: Path) -> dict:
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoMedido("TAXONOMIA_ILEGIBLE %s: %s" % (ruta, exc)) from exc
    if not isinstance(d, dict) or d.get("schema") != "mog.issue-taxonomy.v1":
        raise NoMedido("TAXONOMIA_SCHEMA_INESPERADO")
    return d


def etiquetas_deseadas(tax: dict, retira: bool = True) -> dict[str, dict]:
    """nombre -> {color, description}. Funcion pura: es lo que el banco verifica.

    `retira=False` (modo aditivo) ignora `retiradas`: el vocabulario se crea y no se retira nada.
    """
    prefijos = tax.get("prefijos") or {}
    fuera: dict[str, dict] = {}
    for seccion, clave in PREFIJO_A_SECCION:
        prefijo = prefijos.get(seccion)
        if not prefijo:
            raise NoMedido("PREFIJO_AUSENTE %s" % seccion)
        for nombre, meta in (tax.get(clave) or {}).items():
            fuera[prefijo + nombre] = {
                "color": str((meta or {}).get("color") or "EDEDED").lstrip("#").upper(),
                "description": str((meta or {}).get("descripcion") or "")[:100],
            }
    gate = tax.get("gate") or {}
    if gate.get("etiqueta_rechazo"):
        fuera[gate["etiqueta_rechazo"]] = {
            "color": str(gate.get("color_rechazo") or "B60205").lstrip("#").upper(),
            "description": str(gate.get("descripcion_rechazo") or "")[:100],
        }
    if retira:
        for retirada in tax.get("retiradas") or []:
            fuera.pop(retirada, None)
    return fuera


def gh_json(args: list[str]):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise NoMedido("GH_FALLO %s: %s" % (" ".join(args[:2]), (r.stderr or r.stdout).strip()[:200]))
    try:
        return json.loads(r.stdout or "null")
    except ValueError as exc:
        raise NoMedido("GH_JSON_ILEGIBLE %s" % exc) from exc


def vivas(repo: str) -> dict[str, dict]:
    d = gh_json(["label", "list", "--repo", repo, "--limit", "200", "--json", "name,color,description"])
    if not isinstance(d, list):
        raise NoMedido("ETIQUETAS_NO_MEDIDAS")
    return {str(x.get("name")): x for x in d}


def deriva(deseadas: dict[str, dict], actuales: dict[str, dict]) -> dict[str, list]:
    faltan = sorted(n for n in deseadas if n not in actuales)
    desalineadas = sorted(
        n for n, meta in deseadas.items()
        if n in actuales and (str(actuales[n].get("color") or "").lstrip("#").upper() != meta["color"]
                              or (actuales[n].get("description") or "") != meta["description"])
    )
    return {"faltan": faltan, "desalineadas": desalineadas}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sincroniza el vocabulario de etiquetas de issues")
    ap.add_argument("--taxonomia", default=".github/scripts/issue-taxonomy.active.json")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--aplica", action="store_true")
    ap.add_argument("--aditiva", action="store_true", help="no aplica retiradas: crea el vocabulario y no retira nada")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        tax = carga(Path(a.taxonomia))
        deseadas = etiquetas_deseadas(tax, retira=not a.aditiva)
        estado = deriva(deseadas, vivas(a.repo))
        if a.aplica and (estado["faltan"] or estado["desalineadas"]):
            for nombre in estado["faltan"] + estado["desalineadas"]:
                meta = deseadas[nombre]
                subprocess.run(["gh", "label", "create", nombre, "--repo", a.repo, "--color", meta["color"],
                                "--description", meta["description"], "--force"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            estado = deriva(deseadas, vivas(a.repo))
            estado["aplicado"] = True
    except NoMedido as exc:
        print(json.dumps({"veredicto": "NO_MEDIDO", "causa": str(exc)}, ensure_ascii=False))
        return 2
    if a.aditiva:
        estado["retiradas_ignoradas"] = list(tax.get("retiradas") or [])
    estado["veredicto"] = "SIN_DERIVA" if not (estado["faltan"] or estado["desalineadas"]) else "DERIVA"
    if a.json:
        print(json.dumps(estado, ensure_ascii=False, sort_keys=True))
    else:
        print("%s faltan=%d desalineadas=%d" % (estado["veredicto"], len(estado["faltan"]), len(estado["desalineadas"])))
    return 0 if estado["veredicto"] == "SIN_DERIVA" else 1


if __name__ == "__main__":
    sys.exit(main())
