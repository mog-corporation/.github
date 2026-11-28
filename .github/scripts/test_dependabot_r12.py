#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dependabot_r12.py - banco de la config de Dependabot gobernado (R12/WF14).

Mide dos cosas:

  CONFIG     `.github/dependabot.yml` declara EXACTAMENTE la gobernanza medida en el merge de R12/WF14:
             ecosistema github-actions, semanal lunes 05:30 Europe/Madrid, tope de 2 PRs abiertas,
             grupo solo minor+patch (los major NO entran en lote automatico), prefijo de commit `ci`,
             y las dos etiquetas vivas `dependencies` + `requiere-verificacion`. Si la config se afloja
             (tope arriba, etiqueta fuera, major en el grupo), este banco se pone en ROJO antes del merge.
  MUTANTES   las mismas mutaciones se aplican sobre copias en memoria del dict medido y se exige que el
             auditor las detecte: el banco muerde, no solo pasa.

Falta de fichero o YAML ilegible = NO_MEDIDO (exit 2), nunca PASS.
Se ejecuta con: python3 .github/scripts/test_dependabot_r12.py
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
CONFIG = RAIZ / ".github" / "dependabot.yml"
GATES = RAIZ / ".github" / "workflows" / "gates.yml"

ETIQUETAS = {"dependencies", "requiere-verificacion"}


def audita(cfg: dict) -> list[str]:
    """Contrato de la config de Dependabot. Devuelve causas; vacio = conforme."""
    causas: list[str] = []
    if not isinstance(cfg, dict):
        return ["CONFIG_NO_ES_MAPA"]
    if cfg.get("version") != 2:
        causas.append("VERSION_NO_ES_2")
    ups = cfg.get("updates")
    if not isinstance(ups, list) or len(ups) != 1:
        return causas + ["UPDATES_NO_ES_UNA_LISTA_DE_UN_ELEMENTO"]
    u = ups[0]
    if u.get("package-ecosystem") != "github-actions":
        causas.append("ECOSISTEMA_NO_ES_GITHUB_ACTIONS")
    if u.get("directory") != "/":
        causas.append("DIRECTORIO_NO_ES_RAIZ")
    sch = u.get("schedule") or {}
    if sch.get("interval") != "weekly":
        causas.append("INTERVALO_NO_ES_WEEKLY")
    if sch.get("day") != "monday":
        causas.append("DIA_NO_ES_MONDAY")
    if str(sch.get("time")) != "05:30":
        causas.append("HORA_NO_ES_05_30")
    if sch.get("timezone") != "Europe/Madrid":
        causas.append("ZONA_NO_ES_EUROPE_MADRID")
    lim = u.get("open-pull-requests-limit")
    if not isinstance(lim, int) or lim != 2:
        causas.append("TOPE_PRS_NO_ES_2")
    labels = set(u.get("labels") or [])
    for et in sorted(ETIQUETAS - labels):
        causas.append("ETIQUETA_FUERA: %s" % et)
    if (u.get("commit-message") or {}).get("prefix") != "ci":
        causas.append("PREFIJO_COMMIT_NO_ES_CI")
    grupos = u.get("groups") or {}
    g = grupos.get("actions-minor-and-patch")
    if not isinstance(g, dict):
        causas.append("GRUPO_ACTIONS_MINOR_AND_PATCH_AUSENTE")
    else:
        if g.get("applies-to") != "version-updates":
            causas.append("GRUPO_NO_APLICA_A_VERSION_UPDATES")
        if set(g.get("update-types") or []) != {"minor", "patch"}:
            causas.append("GRUPO_NO_ES_EXACTAMENTE_MINOR_PATCH")
    return causas


def carga() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


class ConfigViva(unittest.TestCase):
    def test_la_config_viva_es_conforme(self):
        self.assertEqual(audita(carga()), [])

    def test_este_banco_corre_en_el_ci_del_bundle(self):
        texto = GATES.read_text(encoding="utf-8")
        self.assertIn("test_dependabot_r12.py", texto,
                      "el banco debe estar cableado en gates.yml o no protege nada")


class Mutantes(unittest.TestCase):
    def setUp(self):
        self.base = carga()

    def mut(self, fn):
        cfg = copy.deepcopy(self.base)
        fn(cfg)
        return audita(cfg)

    def test_tope_arriba_muere(self):
        self.assertIn("TOPE_PRS_NO_ES_2",
                      self.mut(lambda c: c["updates"][0].__setitem__("open-pull-requests-limit", 50)))

    def test_etiqueta_verificacion_fuera_muere(self):
        self.assertIn("ETIQUETA_FUERA: requiere-verificacion",
                      self.mut(lambda c: c["updates"][0].__setitem__(
                          "labels", ["dependencies"])))

    def test_major_en_el_grupo_muere(self):
        self.assertIn("GRUPO_NO_ES_EXACTAMENTE_MINOR_PATCH",
                      self.mut(lambda c: c["updates"][0]["groups"]["actions-minor-and-patch"]
                               .__setitem__("update-types", ["minor", "patch", "major"])))

    def test_ecosistema_cambiado_muere(self):
        self.assertIn("ECOSISTEMA_NO_ES_GITHUB_ACTIONS",
                      self.mut(lambda c: c["updates"][0].__setitem__("package-ecosystem", "pip")))

    def test_sin_grupo_muere(self):
        self.assertIn("GRUPO_ACTIONS_MINOR_AND_PATCH_AUSENTE",
                      self.mut(lambda c: c["updates"][0].pop("groups")))

    def test_auditor_no_es_mudo(self):
        # Control positivo del auditor: un mapa vacio produce causas, no conformidad.
        self.assertTrue(audita({}))


class FailClosed(unittest.TestCase):
    def test_config_ausente_no_es_pass(self):
        global CONFIG
        original = CONFIG
        try:
            CONFIG = AQUI / "dependabot.inexistente.yml"
            with self.assertRaises(FileNotFoundError):
                carga()
        finally:
            CONFIG = original


if __name__ == "__main__":
    if not CONFIG.exists():
        print("NO_MEDIDO: no existe %s" % CONFIG, file=sys.stderr)
        sys.exit(2)
    unittest.main(verbosity=2)