#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""banco_salud_min.py - controles de salud minima de un repo para la puerta `gate-min`.

Seis controles, todos medibles sobre el arbol del repo, sin red y sin criterios de gusto:

  H1  sin bytecode versionado (.pyc / .pyo / __pycache__)
  H2  sin material sensible versionado por nombre (.env, *.pem, *.key, id_rsa*, credentials.json...)
  H3  todo .github/workflows/*.yml parsea y declara `on:` y `jobs:`
  H4  sin ficheros NUEVOS de mas de 1 MiB (los que ya estaban en la base son deuda previa, no del cambio)
  H5  sin claves privadas ni tokens en claro (AWS, GitHub, Google, claves PEM)
  H6  toda accion de terceros (`uses:` en un workflow) va pinneada por SHA40, nunca por rama ni tag

Todos los controles miden lo VERSIONADO (`git ls-files`), no lo que haya suelto en el arbol: los tests dejan
bytecode, el checkout anidado del bundle deja `bundle/`, y nada de eso es deuda del repo que se revisa.

Los controles habilitados y su descripcion salen de `gate-min.active.json`, que tiene que declararlos
EXACTAMENTE: si alguien quita un control del registro, el banco no mide menos, se niega a medir (NO_MEDIDO).
Ese es el fail-closed de esta puerta: no se puede aflojar por configuracion.

Contrato de E/S:
  banco_salud_min.py [--raiz .] [--registro F] [--excluir NOMBRE]... [--base COMMIT] [--json]
  exit 0 = VERDE; exit 1 = DEFECTOS (dice cuales y donde); exit 2 = NO_MEDIDO (no se pudo medir).

H5 es un pre-filtro barato y nombrado; el escaneo de secretos de verdad es gitleaks sobre el rango del PR.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

PESO_MAX = 1024 * 1024
SENSIBLE_RE = re.compile(
    r"(^|/)("
    r"\.env|\.env\.[^/]+|credentials\.json|secrets\.ya?ml|"
    r"[^/]+\.(pem|key|p12|pfx|jks|keystore)|"
    r"id_rsa[^/]*|id_ed25519[^/]*|id_dsa[^/]*"
    r")$",
    re.I,
)
NO_SENSIBLE_RE = re.compile(r"\.(example|sample|template)$", re.I)
CLAVE_PEM_RE = re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----")
TOKEN_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|gh[pousr]_[0-9A-Za-z]{36,}|ya29\.[0-9A-Za-z_.-]{20,}|AIza[0-9A-Za-z_-]{35})"
)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

CONTROLES = {
    "H1": "sin bytecode versionado",
    "H2": "sin material sensible versionado por nombre",
    "H3": "workflows parseables con on y jobs",
    "H4": "sin ficheros nuevos de mas de 1 MiB",
    "H5": "sin claves privadas ni tokens en claro",
    "H6": "acciones de terceros pinneadas por SHA40 (workflows)",
    "H7": "runners self-hosted con su sistema operativo declarado",
}


class NoMedido(Exception):
    """No se pudo medir: la puerta no autoriza."""


def carga_registro(ruta: Path) -> dict:
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoMedido("REGISTRO_ILEGIBLE %s: %s" % (ruta, exc)) from exc
    if not isinstance(d, dict) or d.get("schema") != "mog.gate-min.v1":
        raise NoMedido("REGISTRO_SCHEMA_INESPERADO")
    controles = d.get("controles")
    if not isinstance(controles, dict) or not controles:
        raise NoMedido("REGISTRO_SIN_CONTROLES")
    desconocidos = sorted(set(controles) - set(CONTROLES))
    faltan = sorted(set(CONTROLES) - set(controles))
    if desconocidos or faltan:
        raise NoMedido("CONTROLES_NO_COINCIDEN desconocidos=%s faltan=%s" % (desconocidos, faltan))
    return d


def lista_ficheros(raiz: Path, excluir: tuple[str, ...]) -> list[Path]:
    """Ficheros VERSIONADOS (`git ls-files`), sin symlinks y podando los directorios excluidos.

    Se mide lo que esta en el indice, no lo que haya suelto en el arbol: correr los tests deja
    `__pycache__`, un checkout anidado deja `bundle/`, y eso no es deuda del repo. Sin git no se puede
    distinguir lo versionado de lo demas: NO_MEDIDO, no se mide el arbol entero a ciegas.
    """
    raiz = Path(raiz)
    try:
        salida = subprocess.run(["git", "-C", str(raiz), "ls-files", "-z"],
                                capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise NoMedido("GIT_NO_DISPONIBLE: sin git no se puede medir lo versionado") from exc
    except subprocess.CalledProcessError as exc:
        raise NoMedido("GIT_NO_REPO %s: %s" % (raiz, exc.stderr.decode("utf-8", "replace").strip()[:200])) from exc
    fuera: list[Path] = []
    for nombre in sorted(n for n in salida.stdout.decode("utf-8", "surrogateescape").split("\0") if n):
        rel = Path(nombre)
        if any(parte in excluir for parte in rel.parts):
            continue
        if (raiz / rel).is_symlink():
            continue
        fuera.append(rel)
    return fuera


def _yaml(raiz: Path, rel: Path):
    return yaml.safe_load((Path(raiz) / rel).read_text(encoding="utf-8"))


def _workflows(ficheros: list[Path]) -> list[Path]:
    return [f for f in ficheros if len(f.parts) >= 3 and f.parts[0] == ".github" and f.parts[1] == "workflows"
            and f.suffix in (".yml", ".yaml")]


def h1_bytecode(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    return [str(f) for f in ficheros if f.suffix in (".pyc", ".pyo") or "__pycache__" in f.parts]


def h2_sensibles(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    # Las plantillas explicitas (.env.example, *.sample, *.template) si pueden versionarse: no llevan valor.
    permitidos = set(regla.get("permitidos") or ())
    fuera = []
    for f in ficheros:
        ruta = str(f)
        if ruta in permitidos:
            continue
        if not SENSIBLE_RE.search(ruta):
            continue
        if NO_SENSIBLE_RE.search(f.name):
            continue
        fuera.append(ruta)
    return fuera


def h3_workflows(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    fuera = []
    for f in _workflows(ficheros):
        try:
            d = _yaml(raiz, f)
        except (OSError, yaml.YAMLError) as exc:
            fuera.append("%s: no parsea (%s)" % (f, str(exc).splitlines()[0][:80]))
            continue
        if not isinstance(d, dict):
            fuera.append("%s: no es un mapa YAML" % f)
            continue
        if not (d.get(True) or d.get("on")):
            fuera.append("%s: sin 'on:'" % f)
        if not isinstance(d.get("jobs"), dict) or not d["jobs"]:
            fuera.append("%s: sin 'jobs:'" % f)
    return fuera


def _en_base(raiz: Path, base: str, rel: Path) -> bool:
    return subprocess.run(["git", "-C", str(raiz), "cat-file", "-e", "%s:%s" % (base, rel.as_posix())],
                          capture_output=True).returncode == 0


def h4_peso(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    """Solo lo NUEVO en el rango: un fichero grande que ya estaba en la base es deuda previa, no de este PR.

    Sin base (primer commit, o medida suelta del arbol) no hay con que comparar y se reporta todo lo grande.
    """
    tope = int(regla.get("bytes_max") or PESO_MAX)
    base = str(regla.get("_base") or "")
    fuera = []
    for f in ficheros:
        try:
            n = (Path(raiz) / f).stat().st_size
        except OSError:
            continue
        if n <= tope:
            continue
        if base and _en_base(raiz, base, f):
            continue
        fuera.append("%s: %d bytes > %d%s" % (f, n, tope, "" if not base else " (nuevo en el rango)"))
    return fuera


def h5_claves(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    fuera = []
    for f in ficheros:
        p = Path(raiz) / f
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            texto = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for num, linea in enumerate(texto.splitlines(), 1):
            if CLAVE_PEM_RE.search(linea) or TOKEN_RE.search(linea):
                fuera.append("%s:%d" % (f, num))
    return fuera


def h6_pins(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    fuera = []
    for f in _workflows(ficheros):
        try:
            d = _yaml(raiz, f)
        except (OSError, yaml.YAMLError):
            continue  # H3 ya lo reporta
        if not isinstance(d, dict):
            continue
        for jid, job in (d.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            usos = [job.get("uses")] + [s.get("uses") for s in (job.get("steps") or []) if isinstance(s, dict)]
            for u in usos:
                if not u or str(u).startswith("./"):
                    continue
                u = str(u)
                ref = u.split("@")[-1] if "@" in u else ""
                if not SHA40_RE.match(ref):
                    fuera.append("%s: %s" % (f, u))
    return fuera


SO_SELF_HOSTED = {"linux", "windows", "macos"}


def h7_runner_so(raiz: Path, ficheros: list[Path], regla: dict) -> list[str]:
    """Un `runs-on` self-hosted tiene que declarar el sistema operativo.

    `runs-on: [self-hosted]` casa con cualquier runner del pool: el job puede caer en un
    runner Windows y morir en el primer `bash` (medido: job 130520148624,
    `bash: command not found`; los runs 43019888854 y 43003765825 quedaron rojos por eso).
    Se mide solo lo escrito LITERAL: una expresion (`${{ fromJSON(vars.MOG_RUNS_ON) }}`) no se
    puede resolver aqui, asi que se declara fuera de alcance; no se presume verde ni rojo.
    """
    fuera = []
    for f in _workflows(ficheros):
        try:
            d = _yaml(raiz, f)
        except (OSError, yaml.YAMLError):
            continue  # H3 ya lo reporta
        if not isinstance(d, dict):
            continue
        for jid, job in (d.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            ro = job.get("runs-on")
            if isinstance(ro, str):
                etiquetas = [ro]
            elif isinstance(ro, list) and all(isinstance(x, str) for x in ro):
                etiquetas = ro
            else:
                continue
            if any("${{" in e for e in etiquetas):
                continue
            bajas = {e.strip().lower() for e in etiquetas}
            if "self-hosted" not in bajas or (bajas & SO_SELF_HOSTED):
                continue
            fuera.append("%s: job %s -> runs-on %s sin etiqueta de SO" % (f, jid, etiquetas))
    return fuera


MEDIDORES = {"H1": h1_bytecode, "H2": h2_sensibles, "H3": h3_workflows,
             "H4": h4_peso, "H5": h5_claves, "H6": h6_pins, "H7": h7_runner_so}


def evalua(raiz: Path, registro: dict, excluir: tuple[str, ...] = ("bundle", ".git"),
           base: str | None = None) -> dict:
    raiz = Path(raiz)
    if not raiz.is_dir():
        raise NoMedido("RAIZ_INEXISTENTE %s" % raiz)
    if base:
        p = subprocess.run(["git", "-C", str(raiz), "rev-parse", "--verify", "%s^{commit}" % base],
                           capture_output=True)
        if p.returncode != 0:
            raise NoMedido("BASE_NO_MEDIBLE %s" % base)
    ficheros = lista_ficheros(raiz, excluir)
    resultados = {}
    for nombre, regla in sorted(registro["controles"].items()):
        medidor = MEDIDORES.get(nombre)
        if medidor is None:
            raise NoMedido("CONTROL_DESCONOCIDO %s" % nombre)
        regla = dict(regla) if isinstance(regla, dict) else {}
        regla["_base"] = base or ""
        hallazgos = medidor(raiz, ficheros, regla)
        resultados[nombre] = {"descripcion": CONTROLES[nombre], "estado": "DEFECTOS" if hallazgos else "VERDE",
                              "hallazgos": hallazgos[:50], "total": len(hallazgos)}
    defectos = sum(1 for r in resultados.values() if r["estado"] == "DEFECTOS")
    return {"veredicto": "DEFECTOS" if defectos else "VERDE", "defectos": defectos,
            "base": base or "", "ficheros_medidos": len(ficheros), "controles": resultados}


def main(argv=None) -> int:
    aquidir = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Salud minima de un repo (gate-min)")
    ap.add_argument("--raiz", default=".")
    ap.add_argument("--registro", default=str(aquidir / "gate-min.active.json"))
    ap.add_argument("--excluir", action="append", default=None)
    ap.add_argument("--base", default="", help="commit base del rango revisado (H4 mide solo lo nuevo)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    excluir = tuple(a.excluir) if a.excluir else ("bundle", ".git")
    try:
        registro = carga_registro(Path(a.registro))
        r = evalua(Path(a.raiz), registro, excluir, a.base)
    except NoMedido as exc:
        print(json.dumps({"veredicto": "NO_MEDIDO", "causa": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # fail-closed: una excepcion no autoriza
        print(json.dumps({"veredicto": "NO_MEDIDO", "causa": "ERROR_DE_INSTRUMENTO %s" % exc}, ensure_ascii=False))
        return 2
    if a.json:
        print(json.dumps(r, ensure_ascii=False, sort_keys=True))
    else:
        for nombre, c in sorted(r["controles"].items()):
            print("%s %-6s %-46s %s" % (nombre, c["estado"], c["descripcion"],
                                        "" if c["estado"] == "VERDE" else "%d hallazgo(s): %s"
                                        % (c["total"], ", ".join(c["hallazgos"][:5]))))
        print("%s controles=%d defectos=%d ficheros=%d" % (r["veredicto"], len(r["controles"]), r["defectos"],
                                                           r["ficheros_medidos"]))
    return 0 if r["veredicto"] == "VERDE" else 1


if __name__ == "__main__":
    sys.exit(main())
