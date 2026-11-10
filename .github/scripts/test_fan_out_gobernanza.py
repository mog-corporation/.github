#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fan_out_gobernanza.py - banco de la fan-out: herencia, caller fino e idempotencia.

Mide lo que la fan-out tiene de distinto a un PR normal:

  HERENCIA   un repo sin `.github/ISSUE_TEMPLATE` propio hereda el Ticket canonico de la org; en cuanto define
             la carpeta, la org deja de aplicar por completo (precedencia repo>org). El banco no lo supone:
             lo resuelve con la tabla del registro y, con --mide, contra la API de GitHub.
  CALLER     el caller de cada repo no lleva logica ni vocabulario: solo disparador, permisos de job y pin SHA40.
             Si alguien lo afloja (rama en vez de SHA, bundle_sha incoherente, permisos de mas, un `run:` copiado),
             el banco se pone en ROJO.
  IDEMPOTENCIA  dos corridas sobre el mismo estado no cambian nada: ni el veredicto, ni el comentario, ni el
             vocabulario de etiquetas (que ademas es aditivo: no retira nada).

Se ejecuta con: python3 .github/scripts/test_fan_out_gobernanza.py
Con --mide: consulta la API (gh) y dice, repo por repo, si hereda o tiene plantilla propia.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
REGISTRO = AQUI / "fan-out-issues.active.json"
PLANTILLA_CALLER = RAIZ / ".github" / "plantillas" / "caller-gate-issue.yml"

import valida_ticket_issue as puerta  # noqa: E402

USES_RE = re.compile(r"^mog-corporation/\.github/\.github/workflows/([A-Za-z0-9_.-]+)@(.+)$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
VOCABULARIO_PROHIBIDO = ("FALTA_", "necesita-plantilla", "issue-taxonomy", "valida_ticket_issue", "python3 ")


class NoMedido(Exception):
    pass


def carga(ruta: Path) -> dict:
    """Registro de la fan-out. Sin el no se mide nada: fail-closed."""
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoMedido("REGISTRO_ILEGIBLE %s: %s" % (ruta, exc)) from exc
    if not isinstance(d, dict) or d.get("schema") != "mog.fan-out-issues.v1":
        raise NoMedido("REGISTRO_SCHEMA_INESPERADO")
    for clave in ("repos", "caller", "workflow", "plantillas_org"):
        if not d.get(clave):
            raise NoMedido("REGISTRO_SIN_%s" % clave.upper())
    return d


def plantilla_efectiva(tiene_local: bool, plantillas_org: list[str]) -> list[str]:
    """Precedencia repo>org: con carpeta propia, las plantillas de la org no se usan EN ABSOLUTO.

    No es solo que gane la local: `config.yml` de la org tampoco aplica, y el Ticket deja de existir para ese
    repo. De ahi que el banco distinga HEREDA de PROPIA en vez de suponer que la org cubre a todos.
    """
    return [] if tiene_local else list(plantillas_org)


def veredicto_herencia(estado: dict[str, bool], plantillas_org: list[str]) -> dict:
    """repo -> HEREDA|PROPIA (propia = hay que revisar que lleve los seis campos). Funcion pura."""
    salida = {repo: ("PROPIA" if tiene else "HEREDA") for repo, tiene in sorted(estado.items())}
    return {"repos": salida, "hereda": sum(1 for v in salida.values() if v == "HEREDA"),
            "propias": sorted(r for r, v in salida.items() if v == "PROPIA")}


def mide_herencia(repos: list[str], existe) -> dict[str, bool]:
    """Pregunta por la carpeta local `.github/ISSUE_TEMPLATE` de cada repo. `existe(repo, ruta) -> bool`."""
    return {repo: bool(existe(repo, ".github/ISSUE_TEMPLATE")) for repo in repos}


def errores_caller(doc: dict, cfg: dict) -> list[str]:
    """Contrato del caller fino. Lista vacia = desplegable; cualquier cosa = el banco muerde."""
    errs: list[str] = []
    on = doc.get(True) if isinstance(doc, dict) else None
    tipos = ((on or {}).get("issues") or {}).get("types") if isinstance(on, dict) else None
    if tipos != ["opened", "edited"]:
        errs.append("TRIGGER_INESPERADO: se espera issues[opened, edited], hay %r" % (tipos,))
    if doc.get("permissions") not in (None, {}):
        errs.append("PERMISOS_EN_EL_CALLER: los permisos van a nivel de job, no del workflow")
    jobs = doc.get("jobs") or {}
    if len(jobs) != 1:
        errs.append("JOBS_INESPERADOS: el caller tiene que ser un solo job (%d)" % len(jobs))
    job = next(iter(jobs.values()), {}) if isinstance(jobs, dict) else {}
    for clave in ("steps", "run", "shell", "env", "services"):
        if clave in job:
            errs.append("LOGICA_EN_EL_CALLER: el caller no puede tener '%s' (la puerta es una sola)" % clave)
    uses = str(job.get("uses") or "")
    m = USES_RE.match(uses)
    if not m:
        errs.append("USES_NO_ES_EL_WORKFLOW: %r" % uses)
    else:
        ref = m.group(2)
        placeholder = cfg.get("placeholder")
        if ref == placeholder:
            errs.append("PLACEHOLDER_SIN_PIN: la plantilla no es desplegable hasta sustituir %s" % placeholder)
        elif not SHA40_RE.match(ref):
            errs.append("CITA_NO_PIN: %r no es un SHA40; prohibido rama o tag" % ref)
        pin = (job.get("with") or {}).get(cfg.get("input_pin") or "bundle_sha")
        if pin != ref:
            errs.append("PIN_INCOHERENTE: bundle_sha=%r y uses@%r no son el mismo pin" % (pin, ref))
    if job.get("permissions") != cfg.get("permisos"):
        errs.append("PERMISOS: se espera %r, hay %r" % (cfg.get("permisos"), job.get("permissions")))
    return errs


def cuerpo_caller(sha: str, registro: dict, plantilla: Path = PLANTILLA_CALLER) -> str:
    """Caller listo para el PR de un repo: el unico cambio respecto de la plantilla es el pin."""
    if not SHA40_RE.match(sha or ""):
        raise ValueError("SHA40_REQUERIDO %r" % sha)
    return plantilla.read_text(encoding="utf-8").replace(registro["caller"]["placeholder"], sha)


def texto_prohibido(texto: str) -> list[str]:
    """Vocabulario o logica que no deben aparecer en el caller."""
    bajo = texto.lower()
    return [p for p in VOCABULARIO_PROHIBIDO if p.lower() in bajo]


def _existe_en_github(repo: str, ruta: str) -> bool:
    """404 = el repo no tiene la carpeta (hereda). Cualquier otro fallo es NO_MEDIDO."""
    r = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (repo, ruta)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return True
    if "Not Found" in (r.stderr or "") or "HTTP 404" in (r.stderr or ""):
        return False
    raise NoMedido("NO_MEDIDO %s/%s: %s" % (repo, ruta, (r.stderr or "").strip()[:200]))


def _caller_en_github(repo: str, ruta: str):
    """Contenido crudo del caller del repo, o None si no lo tiene. Otro fallo es NO_MEDIDO."""
    r = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (repo, ruta),
                        "-H", "Accept: application/vnd.github.raw"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return r.stdout
    if "Not Found" in (r.stderr or "") or "HTTP 404" in (r.stderr or ""):
        return None
    raise NoMedido("NO_MEDIDO %s/%s: %s" % (repo, ruta, (r.stderr or "").strip()[:200]))


def audita_callers(repos: list[str], cfg: dict, lee) -> dict:
    """Mide el caller REAL de cada repo (no una copia local). None = todavia no lo tiene."""
    salida = {}
    for repo in repos:
        texto = lee(repo, cfg["ruta"])
        if texto is None:
            salida[repo] = ["SIN_CALLER"]
            continue
        try:
            doc = yaml.safe_load(texto)
        except yaml.YAMLError as exc:
            salida[repo] = ["CALLER_ILEGIBLE: %s" % exc]
            continue
        salida[repo] = errores_caller(doc or {}, cfg)
    return salida


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reg = carga(REGISTRO)
    cfg = dict(reg["caller"], **{"input_pin": reg["workflow"]["input_pin"]})
    if "--audita-callers" in argv:
        try:
            audit = audita_callers(reg["repos"], cfg, _caller_en_github)
        except NoMedido as exc:
            print(json.dumps({"veredicto": "NO_MEDIDO", "causa": str(exc)}, ensure_ascii=False))
            return 2
        veredicto = "CALLERS_OK" if not any(audit.values()) else "CALLER_AFLOJADO"
        print(json.dumps({"veredicto": veredicto, "repos": audit}, ensure_ascii=False, sort_keys=True))
        return 0 if veredicto == "CALLERS_OK" else 1
    if "--mide" not in argv:
        print("uso: test_fan_out_gobernanza.py --mide | --audita-callers")
        return 0
    try:
        estado = mide_herencia(reg["repos"], _existe_en_github)
    except NoMedido as exc:
        print(json.dumps({"veredicto": "NO_MEDIDO", "causa": str(exc)}, ensure_ascii=False))
        return 2
    v = veredicto_herencia(estado, reg["plantillas_org"])
    v["veredicto"] = "HERENCIA_OK" if v["hereda"] == len(reg["repos"]) else "HERENCIA_CON_PLANTILLA_PROPIA"
    print(json.dumps(v, ensure_ascii=False, sort_keys=True))
    return 0 if v["veredicto"] == "HERENCIA_OK" else 1


class Herencia(unittest.TestCase):
    def setUp(self):
        self.reg = carga(REGISTRO)

    def test_un_repo_sin_plantilla_propia_hereda_el_ticket_y_el_config(self):
        efectivas = plantilla_efectiva(False, self.reg["plantillas_org"])
        self.assertIn(".github/ISSUE_TEMPLATE/ticket.yml", efectivas)
        self.assertIn(".github/ISSUE_TEMPLATE/config.yml", efectivas)

    def test_un_repo_con_plantilla_propia_no_hereda_nada(self):
        self.assertEqual(plantilla_efectiva(True, self.reg["plantillas_org"]), [])

    def test_el_flag_de_plantilla_propia_es_load_bearing(self):
        # Mutante: si la herencia ignorase `tiene_local`, este assert dejaria de cumplirse.
        self.assertNotEqual(plantilla_efectiva(True, self.reg["plantillas_org"]),
                            plantilla_efectiva(False, self.reg["plantillas_org"]))

    def test_los_destinos_son_exactamente_los_siete_del_encargo(self):
        self.assertEqual(sorted(self.reg["repos"]), sorted([
            "mog-corporation/mog-control-plane",
            "mog-corporation/mog-containers-m1",
            "mog-corporation/mog-suite",
            "mog-corporation/mogbot-personal-skills",
            "mog-corporation/mogautomation-arch",
            "mog-corporation/mog-data-engine",
            "mog-corporation/mog-federation",
        ]))

    def test_mog_universe_esta_excluido_y_motivado(self):
        excluidos = {e["repo"]: e.get("motivo") for e in self.reg["excluidos"]}
        self.assertIn("mog-corporation/mog-universe", excluidos)
        self.assertTrue(excluidos["mog-corporation/mog-universe"])
        self.assertFalse(set(self.reg["repos"]) & set(excluidos), "un repo no puede ser destino y excluido")

    def test_veredicto_distingue_hereda_de_propia(self):
        v = veredicto_herencia({"a/x": False, "a/y": True}, self.reg["plantillas_org"])
        self.assertEqual(v["repos"]["a/x"], "HEREDA")
        self.assertEqual(v["repos"]["a/y"], "PROPIA")
        self.assertEqual(v["propias"], ["a/y"])

    def test_mide_herencia_por_la_carpeta_y_no_por_el_api_de_community(self):
        llamadas = []

        def existe(repo, ruta):
            llamadas.append((repo, ruta))
            return repo.endswith("mog-suite")

        estado = mide_herencia(self.reg["repos"], existe)
        self.assertTrue(estado["mog-corporation/mog-suite"])
        self.assertFalse(estado["mog-corporation/mog-control-plane"])
        self.assertEqual(len(llamadas), len(self.reg["repos"]))
        self.assertTrue(all(r == ".github/ISSUE_TEMPLATE" for _, r in llamadas))

    def test_mide_aborta_en_no_medido_en_vez_de_suponer(self):
        def roto(repo, ruta):
            raise NoMedido("NO_MEDIDO %s" % repo)

        with self.assertRaises(NoMedido):
            mide_herencia(self.reg["repos"], roto)


class Caller(unittest.TestCase):
    def setUp(self):
        self.reg = carga(REGISTRO)
        self.cfg = dict(self.reg["caller"], **{"input_pin": self.reg["workflow"]["input_pin"]})
        self.texto = PLANTILLA_CALLER.read_text(encoding="utf-8")
        self.sha = "0123456789abcdef0123456789abcdef01234567"

    def doc(self, texto=None):
        return yaml.safe_load(texto if texto is not None else self.texto)

    def test_la_plantilla_todavia_no_es_desplegable(self):
        errs = errores_caller(self.doc(), self.cfg)
        self.assertTrue(any(e.startswith("PLACEHOLDER_SIN_PIN") for e in errs), errs)

    def test_con_pin_sha40_y_bundle_coherente_no_hay_errores(self):
        self.assertEqual(errores_caller(self.doc(cuerpo_caller(self.sha, self.reg)), self.cfg), [])

    def test_una_rama_en_vez_de_sha_es_rojo(self):
        malo = self.texto.replace(self.reg["caller"]["placeholder"], "main")
        self.assertTrue(any(e.startswith("CITA_NO_PIN") for e in errores_caller(self.doc(malo), self.cfg)))

    def test_un_tag_tampoco_vale(self):
        malo = self.texto.replace(self.reg["caller"]["placeholder"], "v1")
        self.assertTrue(any(e.startswith("CITA_NO_PIN") for e in errores_caller(self.doc(malo), self.cfg)))

    def test_bundle_sha_distinto_del_uses_es_rojo(self):
        texto = cuerpo_caller(self.sha, self.reg).replace(
            "bundle_sha: %s" % self.sha, "bundle_sha: %s" % ("f" * 40))
        self.assertTrue(any(e.startswith("PIN_INCOHERENTE") for e in errores_caller(self.doc(texto), self.cfg)))

    def test_bundle_sha_vacio_es_rojo(self):
        texto = cuerpo_caller(self.sha, self.reg).replace("bundle_sha: %s" % self.sha, "bundle_sha: ''")
        self.assertTrue(any(e.startswith("PIN_INCOHERENTE") for e in errores_caller(self.doc(texto), self.cfg)))

    def test_sin_issues_write_no_puede_comentar_ni_etiquetar(self):
        malo = cuerpo_caller(self.sha, self.reg).replace("      issues: write\n", "")
        self.assertTrue(any(e.startswith("PERMISOS") for e in errores_caller(self.doc(malo), self.cfg)))

    def test_permisos_de_mas_son_rojo(self):
        malo = cuerpo_caller(self.sha, self.reg).replace("issues: write", "issues: write\n      contents: write")
        errs = errores_caller(self.doc(malo), self.cfg)
        self.assertTrue(any(e.startswith("PERMISOS") for e in errs), errs)

    def test_permisos_en_el_workflow_y_no_en_el_job_es_rojo(self):
        doc = self.doc(cuerpo_caller(self.sha, self.reg))
        doc["permissions"] = {"issues": "write"}
        self.assertTrue(any(e.startswith("PERMISOS_EN_EL_CALLER") for e in errores_caller(doc, self.cfg)))

    def test_copiar_la_logica_al_caller_es_rojo(self):
        doc = self.doc(cuerpo_caller(self.sha, self.reg))
        doc["jobs"]["puerta"]["steps"] = [{"run": "python3 valida_ticket_issue.py"}]
        self.assertTrue(any(e.startswith("LOGICA_EN_EL_CALLER") for e in errores_caller(doc, self.cfg)))

    def test_un_disparador_que_no_sea_issues_es_rojo(self):
        doc = self.doc(cuerpo_caller(self.sha, self.reg))
        doc[True] = {"push": {"branches": ["main"]}}
        self.assertTrue(any(e.startswith("TRIGGER_INESPERADO") for e in errores_caller(doc, self.cfg)))

    def test_la_plantilla_no_hornea_vocabulario(self):
        self.assertEqual(texto_prohibido(self.texto), [])

    def test_el_caller_no_tiene_pasos_ni_scripts(self):
        doc = self.doc(cuerpo_caller(self.sha, self.reg))
        job = doc["jobs"]["puerta"]
        self.assertNotIn("steps", job)
        self.assertTrue(job["uses"].startswith("mog-corporation/.github/.github/workflows/valida-ticket.yml@"))

    def test_cuerpo_caller_sustituye_los_dos_pines(self):
        texto = cuerpo_caller(self.sha, self.reg)
        self.assertNotIn(self.reg["caller"]["placeholder"], texto)
        self.assertEqual(texto.count(self.sha), 2)


class Idempotencia(unittest.TestCase):
    def setUp(self):
        self.reg = carga(REGISTRO)
        self.cfg = dict(self.reg["caller"], **{"input_pin": self.reg["workflow"]["input_pin"]})
        self.sha = "0123456789abcdef0123456789abcdef01234567"

    def test_el_mismo_sha_produce_el_mismo_caller(self):
        self.assertEqual(cuerpo_caller(self.sha, self.reg), cuerpo_caller(self.sha, self.reg))

    def test_medir_dos_veces_da_el_mismo_veredicto(self):
        def existe(repo, ruta):
            return False

        a = veredicto_herencia(mide_herencia(self.reg["repos"], existe), self.reg["plantillas_org"])
        b = veredicto_herencia(mide_herencia(self.reg["repos"], existe), self.reg["plantillas_org"])
        self.assertEqual(a, b)
        self.assertEqual(a["hereda"], len(self.reg["repos"]))

    def test_el_comentario_de_rechazo_no_lleva_fecha_ni_contador(self):
        texto = puerta.cuerpo_rechazo(["FALTA_WRITER"], "necesita-plantilla")
        self.assertIsNone(re.search(r"\b20\d\d-\d\d-\d\d\b", texto))
        self.assertEqual(texto, puerta.cuerpo_rechazo(["FALTA_WRITER"], "necesita-plantilla"))

    def test_la_taxonomia_de_fan_out_es_aditiva(self):
        tax = json.loads((AQUI / "issue-taxonomy.active.json").read_text(encoding="utf-8"))
        self.assertEqual(tax.get("retiradas"), [])
        self.assertFalse(self.reg["etiquetas"]["retira"])

    def test_el_registro_no_repite_repos(self):
        self.assertEqual(len(self.reg["repos"]), len(set(self.reg["repos"])))
        self.assertTrue(all(r.startswith("mog-corporation/") for r in self.reg["repos"]))


class AuditoriaDeCallers(unittest.TestCase):
    """Mide el caller que hay en cada repo: es lo que muerde si un repo se afloja despues del merge."""

    def setUp(self):
        self.reg = carga(REGISTRO)
        self.cfg = dict(self.reg["caller"], **{"input_pin": self.reg["workflow"]["input_pin"]})
        self.sha = "0123456789abcdef0123456789abcdef01234567"

    def test_un_repo_sin_caller_se_reporta_y_no_se_supone(self):
        audit = audita_callers(["a/x"], self.cfg, lambda repo, ruta: None)
        self.assertEqual(audit["a/x"], ["SIN_CALLER"])

    def test_un_caller_aflojado_se_reporta_con_su_causa(self):
        flojo = cuerpo_caller(self.sha, self.reg).replace(self.sha, "main")
        audit = audita_callers(["a/x"], self.cfg, lambda repo, ruta: flojo)
        self.assertTrue(any(e.startswith("CITA_NO_PIN") for e in audit["a/x"]), audit)

    def test_un_caller_bien_pinneado_pasa(self):
        bueno = cuerpo_caller(self.sha, self.reg)
        self.assertEqual(audita_callers(["a/x"], self.cfg, lambda repo, ruta: bueno), {"a/x": []})

    def test_un_caller_ilegible_no_autoriza(self):
        audit = audita_callers(["a/x"], self.cfg, lambda repo, ruta: "name: [sin cerrar")
        self.assertTrue(audit["a/x"][0].startswith("CALLER_ILEGIBLE"), audit)


class ContratoDelBundle(unittest.TestCase):
    def setUp(self):
        self.reg = carga(REGISTRO)
        self.wf = yaml.safe_load((RAIZ / self.reg["workflow"]["ruta"]).read_text(encoding="utf-8"))
        self.texto = (RAIZ / self.reg["workflow"]["ruta"]).read_text(encoding="utf-8")

    def test_el_workflow_es_reutilizable_y_exige_el_pin(self):
        entrada = self.wf[True]["workflow_call"]["inputs"][self.reg["workflow"]["input_pin"]]
        self.assertTrue(entrada["required"])

    def test_el_workflow_no_recorta_los_permisos_del_caller(self):
        self.assertNotIn("permissions", self.wf)

    def test_el_workflow_lee_del_bundle_y_no_de_copias_locales(self):
        self.assertIn("bundle/.github/scripts/valida_ticket_issue.py", self.texto)
        self.assertIn("bundle/.github/scripts/issue-taxonomy.active.json", self.texto)

    def test_el_banco_corre_en_el_ci_del_bundle(self):
        gates = (RAIZ / ".github" / "workflows" / "gates.yml").read_text(encoding="utf-8")
        self.assertIn("test_valida_ticket_issue.py", gates)
        self.assertIn("test_fan_out_gobernanza.py", gates)


if __name__ == "__main__":
    if any(a.startswith("--") for a in sys.argv[1:]):
        sys.exit(main())
    unittest.main(verbosity=2)
