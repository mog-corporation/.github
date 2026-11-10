#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gate_min.py - banco de la puerta minima: positivo, negativo por control y mutantes.

Mide tres cosas:

  REGISTRO   `gate-min.active.json` declara EXACTAMENTE los seis controles: si se quita uno, el banco no mide
             menos, se niega a medir (NO_MEDIDO, exit 2). Ese es el fail-closed de la puerta.
  CONTROLES  H1-H6 muerden de verdad: cada control tiene su fixture que lo pone en rojo, y un repo sano queda
             verde. Ademas los excluidos (`bundle`, `.git`) y las plantillas `.env.example` no se confunden.
  MUTANTES   se mutan el workflow reutilizable, el caller y la accion de gitleaks sobre el texto revisado y se
             exige que el banco lo detecte: job renombrado, runs-on por variable, permisos de mas, `paths`,
             pin por rama, escaneo del arbol entero, digest flojo. El banco muerde, no solo pasa.

Se ejecuta con: python3 .github/scripts/test_gate_min.py
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
REGISTRO = AQUI / "gate-min.active.json"
WORKFLOW = RAIZ / ".github" / "workflows" / "gate-min.yml"
PLANTILLA = RAIZ / ".github" / "plantillas" / "caller-gate-min.yml"
ACCION = RAIZ / ".github" / "actions" / "ensure-gitleaks" / "action.yml"

import banco_salud_min as banco  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(r"^mog-corporation/\.github/\.github/workflows/([A-Za-z0-9_.-]+)@(.+)$")
REPOS_ESPERADOS = [
    "mog-corporation/mog-control-plane",
    "mog-corporation/mogbot-personal-skills",
    "mog-corporation/mogautomation-arch",
    "mog-corporation/mog-containers-m1",
    "mog-corporation/mog-suite",
]

# Literales partidos a proposito: este fichero vive en el arbol que H5 escanea y no debe autoreportarse.
AWS = "AKIA" + "Q" * 16
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
GHTOKEN = "ghp" + "_" + "b" * 36

SANO = {
    "README.md": "# repo\n",
    "app/main.py": "print('hola')\n",
    ".env.example": "TOKEN=\n",
    ".github/workflows/ci.yml": (
        "on:\n  push:\n    branches: [main]\n"
        "jobs:\n  ci:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
    ),
}


def escribe(tmp: Path, ficheros: dict) -> Path:
    for rel, contenido in ficheros.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contenido, bytes):
            p.write_bytes(contenido)
        else:
            p.write_text(contenido, encoding="utf-8")
    return tmp


def repo(ficheros: dict) -> Path:
    """Arbol versionado: el banco mide `git ls-files`, no lo que haya suelto en el directorio."""
    tmp = Path(tempfile.mkdtemp(prefix="gatemin-"))
    escribe(tmp, ficheros)
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "core.excludesFile=/dev/null", "add", "-A", "-f"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"],
                   check=True, capture_output=True)
    return tmp


def evalua(raiz: Path) -> dict:
    return banco.evalua(raiz, banco.carga_registro(REGISTRO))


def corre(args: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = banco.main(args)
    return rc, out.getvalue()


def audita_workflow(texto: str, reg: dict) -> list[str]:
    """Contrato del workflow reutilizable. Devuelve causas; vacio = conforme."""
    try:
        d = yaml.safe_load(texto)
    except yaml.YAMLError as exc:
        return ["WORKFLOW_ILEGIBLE %s" % str(exc).splitlines()[0][:80]]
    if not isinstance(d, dict):
        return ["WORKFLOW_NO_ES_MAPA"]
    errores = []
    if "permissions" in d:
        errores.append("WORKFLOW_DECLARA_PERMISSIONS (recortaria los del caller)")
    wc = ((d.get(True) or {}).get("workflow_call") or {})
    entrada = (wc.get("inputs") or {}).get(reg["workflow"]["input_pin"]) or {}
    if entrada.get("required") is not True:
        errores.append("INPUT_PIN_NO_OBLIGATORIO")
    jobs = d.get("jobs") or {}
    if list(jobs) != [reg["workflow"]["job"]]:
        errores.append("JOBS_INESPERADOS %s (el contexto de check es un job unico y fijo)" % list(jobs))
    job = jobs.get(reg["workflow"]["job"]) or {}
    if job.get("runs-on") != "ubuntu-latest":
        errores.append("RUNS_ON_NO_FIJO %r (tiene que ser literal)" % (job.get("runs-on"),))
    for pieza in ("banco_salud_min.py", "gate-min.active.json"):
        if pieza not in texto:
            errores.append("SIN_%s" % pieza.upper())
    if "uses: ./bundle/.github/actions/ensure-gitleaks" not in texto:
        errores.append("SIN_ENSURE_GITLEAKS")
    if "merge-base" not in texto or "--log-opts" not in texto:
        errores.append("GITLEAKS_SIN_RANGO")
    if "--no-git" in texto:
        errores.append("GITLEAKS_ESCANEA_ARBOL (arrastra deuda ajena al PR)")
    if "ref: ${{ inputs.bundle_sha }}" not in texto:
        errores.append("BUNDLE_NO_PINNEADO_AL_INPUT")
    if '--base "$BASE"' not in texto or "BASE: ${{ steps.rango.outputs.base }}" not in texto:
        errores.append("BANCO_SIN_BASE (H4 y gitleaks no comparten rango)")
    return errores


def audita_caller(texto: str, reg: dict) -> list[str]:
    """Contrato del caller fino. Devuelve causas; vacio = conforme."""
    try:
        d = yaml.safe_load(texto)
    except yaml.YAMLError as exc:
        return ["CALLER_ILEGIBLE %s" % str(exc).splitlines()[0][:80]]
    if not isinstance(d, dict):
        return ["CALLER_NO_ES_MAPA"]
    errores = []
    ev = d.get(True) or d.get("on") or {}
    if not isinstance(ev, dict):
        return ["CALLER_EVENTOS_INVALIDOS"]
    if "pull_request" not in ev:
        errores.append("SIN_PULL_REQUEST")
    pr = ev.get("pull_request")
    if isinstance(pr, dict) and "paths" in pr:
        errores.append("CALLER_CON_PATHS (el check dejaria de emitirse en algunos PR)")
    if "workflow_dispatch" not in ev:
        errores.append("SIN_WORKFLOW_DISPATCH")
    if d.get("permissions") != {}:
        errores.append("CALLER_PERMISOS_GLOBALES %r (tiene que ser {})" % (d.get("permissions"),))
    jobs = d.get("jobs") or {}
    if list(jobs) != [reg["workflow"]["job"]]:
        errores.append("CALLER_JOBS_INESPERADOS %s" % list(jobs))
    job = jobs.get(reg["workflow"]["job"]) or {}
    if job.get("permissions") != {"contents": "read"}:
        errores.append("CALLER_PERMISOS_DE_JOB %r (solo contents:read)" % (job.get("permissions"),))
    if "run" in job:
        errores.append("CALLER_CON_LOGICA (run:)")
    m = USES_RE.match(str(job.get("uses") or ""))
    if not m:
        errores.append("CALLER_USES_INVALIDO %r" % job.get("uses"))
        return errores
    fichero, ref = m.groups()
    if fichero != "gate-min.yml":
        errores.append("CALLER_WORKFLOW_INESPERADO %s" % fichero)
    if not SHA40_RE.match(ref):
        errores.append("CITA_NO_PIN %s" % ref)
    if (job.get("with") or {}).get("bundle_sha") != ref:
        errores.append("BUNDLE_SHA_INCOHERENTE")
    return errores


class ContratoDelRegistro(unittest.TestCase):
    def setUp(self):
        self.reg = banco.carga_registro(REGISTRO)

    def test_schema_y_los_seis_controles(self):
        self.assertEqual(self.reg["schema"], "mog.gate-min.v1")
        self.assertEqual(sorted(self.reg["controles"]), sorted(banco.CONTROLES))

    def test_los_cinco_repos(self):
        self.assertEqual(sorted(self.reg["repos"]), sorted(REPOS_ESPERADOS))

    def test_contexto_estable_y_ubuntu_literal(self):
        self.assertEqual(self.reg["workflow"]["job"], "gate-min")
        self.assertEqual(self.reg["workflow"]["contexto"], self.reg["workflow"]["job"])
        self.assertEqual(self.reg["workflow"]["runs_on"], "ubuntu-latest")

    def test_gitleaks_por_rango_y_limite_declarado(self):
        g = self.reg["gitleaks"]
        self.assertFalse(g["historia_completa"])
        self.assertEqual(g["modo"], "rango base..head")
        self.assertIn("limite_conocido", g)

    def test_falta_un_control_no_mide(self):
        for control in list(banco.CONTROLES):
            with self.subTest(control=control):
                roto = dict(self.reg)
                roto["controles"] = {k: v for k, v in self.reg["controles"].items() if k != control}
                tmp = Path(tempfile.mkdtemp(prefix="gatemin-reg-")) / "r.json"
                tmp.write_text(json.dumps(roto), encoding="utf-8")
                with self.assertRaisesRegex(banco.NoMedido, "CONTROLES_NO_COINCIDEN"):
                    banco.carga_registro(tmp)

    def test_control_desconocido_no_mide(self):
        roto = json.loads(REGISTRO.read_text(encoding="utf-8"))
        roto["controles"]["H7"] = {}
        tmp = Path(tempfile.mkdtemp(prefix="gatemin-reg-")) / "r.json"
        tmp.write_text(json.dumps(roto), encoding="utf-8")
        with self.assertRaisesRegex(banco.NoMedido, "CONTROLES_NO_COINCIDEN"):
            banco.carga_registro(tmp)

    def test_schema_inesperado_no_mide(self):
        tmp = Path(tempfile.mkdtemp(prefix="gatemin-reg-")) / "r.json"
        tmp.write_text(json.dumps({"schema": "otro/v9", "controles": self.reg["controles"]}), encoding="utf-8")
        with self.assertRaisesRegex(banco.NoMedido, "REGISTRO_SCHEMA_INESPERADO"):
            banco.carga_registro(tmp)

    def test_registro_ilegible_no_mide(self):
        tmp = Path(tempfile.mkdtemp(prefix="gatemin-reg-")) / "r.json"
        tmp.write_text("{no es json", encoding="utf-8")
        with self.assertRaisesRegex(banco.NoMedido, "REGISTRO_ILEGIBLE"):
            banco.carga_registro(tmp)


class BancoPositivo(unittest.TestCase):
    def test_repo_sano_verde(self):
        r = evalua(repo(dict(SANO)))
        self.assertEqual(r["veredicto"], "VERDE", r)
        self.assertEqual(r["defectos"], 0)
        self.assertEqual(sorted(r["controles"]), sorted(banco.CONTROLES))

    def test_plantilla_env_no_es_sensible(self):
        r = evalua(repo(dict(SANO, **{"config/.env.example": "X=\n", "config/app.env.template": "X=\n"})))
        self.assertEqual(r["veredicto"], "VERDE", r)

    def test_bundle_y_git_se_excluyen(self):
        r = evalua(repo(dict(SANO, **{"bundle/.env": "X=1\n", ".git/config": "x\n"})))
        self.assertEqual(r["veredicto"], "VERDE", r)

    def test_plantillas_no_se_escanean_por_h6(self):
        r = evalua(repo(dict(SANO, **{".github/plantillas/caller.yml": "jobs:\n  x:\n    uses: a/b@main\n"})))
        self.assertEqual(r["veredicto"], "VERDE", r)


class ControlesQueMuerden(unittest.TestCase):
    def assertSoloRojo(self, ficheros: dict, control: str):
        r = evalua(repo(ficheros))
        self.assertEqual(r["veredicto"], "DEFECTOS", r)
        self.assertEqual(sorted(k for k, v in r["controles"].items() if v["estado"] == "DEFECTOS"), [control], r)
        return r["controles"][control]["hallazgos"]

    def test_h1_bytecode(self):
        h = self.assertSoloRojo(dict(SANO, **{"app/__pycache__/main.cpython-312.pyc": b"\x00"}), "H1")
        self.assertTrue(any("main.cpython-312.pyc" in x for x in h), h)

    def test_h1_no_flagea_bytecode_no_versionado(self):
        ruta = repo(dict(SANO))
        (ruta / "app" / "__pycache__").mkdir(parents=True)
        (ruta / "app" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x00")
        r = evalua(ruta)
        self.assertEqual(r["veredicto"], "VERDE", r)

    def test_h2_material_sensible(self):
        h = self.assertSoloRojo(dict(SANO, **{".env": "SECRETO=1\n"}), "H2")
        self.assertIn(".env", h)
        h = self.assertSoloRojo(dict(SANO, **{"certs/server.pem": "contenido sin clave\n"}), "H2")
        self.assertIn("certs/server.pem", h)
        h = self.assertSoloRojo(dict(SANO, **{"deploy/id_rsa": "x\n"}), "H2")
        self.assertIn("deploy/id_rsa", h)
        h = self.assertSoloRojo(dict(SANO, **{"conf/credentials.json": "{}\n"}), "H2")
        self.assertIn("conf/credentials.json", h)

    def test_h3_workflow_roto(self):
        h = self.assertSoloRojo(dict(SANO, **{".github/workflows/roto.yml": "name: [sin cerrar\n"}), "H3")
        self.assertTrue(any("no parsea" in x for x in h), h)
        h = self.assertSoloRojo(dict(SANO, **{".github/workflows/sin_on.yml": "jobs:\n  a:\n    runs-on: x\n"}), "H3")
        self.assertTrue(any("sin 'on:'" in x for x in h), h)
        h = self.assertSoloRojo(dict(SANO, **{".github/workflows/sin_jobs.yml": "on: push\n"}), "H3")
        self.assertTrue(any("sin 'jobs:'" in x for x in h), h)

    def test_h4_peso(self):
        h = self.assertSoloRojo(dict(SANO, **{"datos/gordo.bin": b"x" * (banco.PESO_MAX + 1)}), "H4")
        self.assertTrue(any("gordo.bin" in x for x in h), h)

    def test_h4_no_flagea_deuda_previa(self):
        ruta = repo(dict(SANO, **{"datos/gordo.bin": b"x" * (banco.PESO_MAX + 1)}))
        r = banco.evalua(ruta, banco.carga_registro(REGISTRO), base="HEAD")
        self.assertEqual(r["veredicto"], "VERDE", r)

    def test_h4_flagea_lo_nuevo_del_rango(self):
        ruta = repo(dict(SANO))
        base = subprocess.run(["git", "-C", str(ruta), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        (ruta / "datos").mkdir()
        (ruta / "datos" / "gordo.bin").write_bytes(b"x" * (banco.PESO_MAX + 1))
        subprocess.run(["git", "-C", str(ruta), "add", "-A"], check=True, capture_output=True)
        r = banco.evalua(ruta, banco.carga_registro(REGISTRO), base=base)
        self.assertEqual(r["veredicto"], "DEFECTOS", r)
        self.assertTrue(any("gordo.bin" in x for x in r["controles"]["H4"]["hallazgos"]), r)

    def test_h5_claves_y_tokens(self):
        h = self.assertSoloRojo(dict(SANO, **{
            "app/creds.txt": "linea1\n" + PEM + "\n",
            "app/aws.txt": AWS + "\n",
            "app/gh.txt": "token=" + GHTOKEN + "\n",
        }), "H5")
        self.assertIn("app/creds.txt:2", h)
        self.assertIn("app/aws.txt:1", h)
        self.assertIn("app/gh.txt:1", h)

    def test_h6_pins(self):
        h = self.assertSoloRojo(dict(SANO, **{
            ".github/workflows/flojo.yml": (
                "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - uses: docker/build-push-action@main\n"
            )
        }), "H6")
        self.assertTrue(any("@v4" in x for x in h), h)
        self.assertTrue(any("@main" in x for x in h), h)
        h = self.assertSoloRojo(dict(SANO, **{
            ".github/workflows/job_flojo.yml": (
                "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    uses: org/repo/.github/workflows/x.yml@v1\n"
            )
        }), "H6")
        self.assertTrue(any("@v1" in x for x in h), h)

    def test_h6_acepta_local_y_sha40(self):
        r = evalua(repo(dict(SANO, **{
            ".github/workflows/ok.yml": (
                "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: ./bundle/.github/actions/ensure-gitleaks\n"
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
            )
        })))
        self.assertEqual(r["veredicto"], "VERDE", r)


class FailClosed(unittest.TestCase):
    def test_exit_0_verde(self):
        rc, salida = corre(["--raiz", str(repo(dict(SANO))), "--registro", str(REGISTRO), "--json"])
        self.assertEqual(rc, 0, salida)
        self.assertIn('"veredicto": "VERDE"', salida)

    def test_exit_1_defectos(self):
        rc, salida = corre(["--raiz", str(repo(dict(SANO, **{".env": "X=1\n"}))), "--registro", str(REGISTRO)])
        self.assertEqual(rc, 1, salida)
        self.assertIn("DEFECTOS", salida)

    def test_exit_2_registro_roto(self):
        tmp = Path(tempfile.mkdtemp(prefix="gatemin-reg-")) / "r.json"
        tmp.write_text(json.dumps({"schema": "mog.gate-min.v1", "controles": {"H1": {}}}), encoding="utf-8")
        rc, salida = corre(["--raiz", str(repo(dict(SANO))), "--registro", str(tmp)])
        self.assertEqual(rc, 2, salida)
        self.assertIn("NO_MEDIDO", salida)

    def test_base_inexistente_no_mide(self):
        with self.assertRaisesRegex(banco.NoMedido, "BASE_NO_MEDIBLE"):
            banco.evalua(repo(dict(SANO)), banco.carga_registro(REGISTRO), base="0" * 40)

    def test_sin_git_no_mide(self):
        tmp = Path(tempfile.mkdtemp(prefix="gatemin-nogit-"))
        with self.assertRaisesRegex(banco.NoMedido, "GIT_NO_REPO"):
            banco.evalua(tmp, banco.carga_registro(REGISTRO))

    def test_exit_2_raiz_inexistente(self):
        rc, salida = corre(["--raiz", "/no/existe/never", "--registro", str(REGISTRO)])
        self.assertEqual(rc, 2, salida)
        self.assertIn("RAIZ_INEXISTENTE", salida)

    def test_exit_2_si_el_instrumento_revienta(self):
        original = banco.MEDIDORES["H2"]
        banco.MEDIDORES["H2"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("medidor roto"))
        try:
            rc, salida = corre(["--raiz", str(repo(dict(SANO))), "--registro", str(REGISTRO)])
        finally:
            banco.MEDIDORES["H2"] = original
        self.assertEqual(rc, 2, salida)
        self.assertIn("ERROR_DE_INSTRUMENTO", salida)


class ContratoDelWorkflow(unittest.TestCase):
    def setUp(self):
        self.reg = banco.carga_registro(REGISTRO)
        self.texto = WORKFLOW.read_text(encoding="utf-8")

    def test_conforme(self):
        self.assertEqual(audita_workflow(self.texto, self.reg), [])

    def test_mutante_job_renombrado(self):
        flojo = self.texto.replace("\n  gate-min:\n", "\n  gate:\n")
        self.assertIn("JOBS_INESPERADOS", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_runs_on_por_variable(self):
        flojo = self.texto.replace("runs-on: ubuntu-latest", "runs-on: ${{ vars.MOG_RUNS_ON || 'ubuntu-latest' }}")
        self.assertIn("RUNS_ON_NO_FIJO", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_permisos_en_el_workflow(self):
        flojo = self.texto.replace("\njobs:\n", "\npermissions:\n  contents: read\n\njobs:\n")
        self.assertIn("WORKFLOW_DECLARA_PERMISSIONS", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_escaneo_del_arbol(self):
        flojo = self.texto.replace('--log-opts="${BASE}..HEAD"', '--no-git')
        self.assertIn("GITLEAKS_ESCANEA_ARBOL", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_sin_rango(self):
        flojo = self.texto.replace("merge-base", "rev-parse")
        self.assertIn("GITLEAKS_SIN_RANGO", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_bundle_por_rama(self):
        flojo = self.texto.replace("ref: ${{ inputs.bundle_sha }}", "ref: main")
        self.assertIn("BUNDLE_NO_PINNEADO_AL_INPUT", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_sin_base_compartida(self):
        flojo = self.texto.replace('--base "$BASE"', "")
        self.assertIn("BANCO_SIN_BASE", " ".join(audita_workflow(flojo, self.reg)))

    def test_mutante_sin_ensure_gitleaks(self):
        flojo = self.texto.replace("uses: ./bundle/.github/actions/ensure-gitleaks",
                                   "uses: ./bundle/.github/actions/nada")
        self.assertIn("SIN_ENSURE_GITLEAKS", " ".join(audita_workflow(flojo, self.reg)))


class ContratoDelCaller(unittest.TestCase):
    def setUp(self):
        self.reg = banco.carga_registro(REGISTRO)
        self.texto = PLANTILLA.read_text(encoding="utf-8")
        self.bueno = self.texto.replace("<SHA40_DEL_MERGE>", SHA)

    def test_conforme(self):
        self.assertEqual(audita_caller(self.bueno, self.reg), [])

    def test_la_plantilla_solo_tiene_los_dos_placeholders(self):
        self.assertEqual(self.texto.count("<SHA40_DEL_MERGE>"), 2)
        self.assertEqual(self.bueno.count(SHA), 2)

    def test_mutante_pin_por_rama(self):
        flojo = self.bueno.replace("@" + SHA, "@main")
        self.assertIn("CITA_NO_PIN", " ".join(audita_caller(flojo, self.reg)))

    def test_mutante_bundle_sha_incoherente(self):
        flojo = self.bueno.replace("bundle_sha: " + SHA, "bundle_sha: " + "f" * 40)
        self.assertIn("BUNDLE_SHA_INCOHERENTE", " ".join(audita_caller(flojo, self.reg)))

    def test_mutante_permisos_de_mas(self):
        flojo = self.bueno.replace("      contents: read\n", "      contents: read\n      issues: write\n")
        self.assertIn("CALLER_PERMISOS_DE_JOB", " ".join(audita_caller(flojo, self.reg)))

    def test_mutante_paths(self):
        flojo = self.bueno.replace("    types: [opened, synchronize, reopened]\n",
                                   "    types: [opened, synchronize, reopened]\n    paths: ['docs/**']\n")
        self.assertIn("CALLER_CON_PATHS", " ".join(audita_caller(flojo, self.reg)))

    def test_mutante_sin_dispatch(self):
        flojo = self.bueno.replace("  workflow_dispatch:\n", "")
        self.assertIn("SIN_WORKFLOW_DISPATCH", " ".join(audita_caller(flojo, self.reg)))

    def test_mutante_con_logica(self):
        flojo = self.bueno.replace("    uses: mog-corporation", "    run: echo hola\n    uses: mog-corporation")
        self.assertIn("CALLER_CON_LOGICA", " ".join(audita_caller(flojo, self.reg)))

    def test_un_caller_ilegible_no_autoriza(self):
        self.assertTrue(audita_caller("name: [sin cerrar", self.reg)[0].startswith("CALLER_ILEGIBLE"))


class AccionDeGitleaks(unittest.TestCase):
    def setUp(self):
        self.reg = banco.carga_registro(REGISTRO)
        self.texto = ACCION.read_text(encoding="utf-8")
        self.wf = WORKFLOW.read_text(encoding="utf-8")

    def test_version_coincide_con_el_registro_y_el_workflow(self):
        self.assertIn('version: "%s"' % self.reg["gitleaks"]["version"], self.wf)
        self.assertIn("GITLEAKS_VERSION", self.texto)

    def test_digests_de_64_hex_y_verificacion(self):
        digests = re.findall(r'expected_sha="([0-9a-f]{64})"', self.texto)
        self.assertEqual(len(digests), 2, digests)
        self.assertIn("sha256sum -c -", self.texto)

    def test_no_usa_gitleaks_action(self):
        self.assertNotIn("gitleaks/gitleaks-action", self.texto)

    def test_version_desconocida_falla(self):
        self.assertIn("sin digest confiable", self.texto)
        self.assertIn("*)", self.texto)


class Integracion(unittest.TestCase):
    def test_el_banco_corre_en_el_ci_del_bundle(self):
        gates = (RAIZ / ".github" / "workflows" / "gates.yml").read_text(encoding="utf-8")
        self.assertIn("test_gate_min.py", gates)

    def test_el_bundle_pasa_su_propio_banco(self):
        r = evalua(RAIZ)
        self.assertEqual(r["veredicto"], "VERDE", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
