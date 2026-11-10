#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_valida_ticket_issue.py - banco de la puerta de issues canonica de la organizacion.

Regla del banco MoG: control positivo, control negativo y mutante. El mutante es el que manda: si se quita un
campo obligatorio del registro gobernado, este banco tiene que ponerse en ROJO. Por eso hay dos clases de test:
los que miden el comportamiento (positivo/negativo) y los que fijan el contrato (el conjunto exacto de
requeridos, la sincronia formulario<->registro y el fail-closed del workflow reutilizable), que son los que
muerden cuando alguien afloja la puerta.

Se ejecuta con: python3 .github/scripts/test_valida_ticket_issue.py
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
TAXONOMIA = RAIZ / ".github" / "scripts" / "issue-taxonomy.active.json"
FORMULARIO = RAIZ / ".github" / "ISSUE_TEMPLATE" / "ticket.yml"
CONFIG = RAIZ / ".github" / "ISSUE_TEMPLATE" / "config.yml"
WORKFLOW = RAIZ / ".github" / "workflows" / "valida-ticket.yml"

import sincroniza_etiquetas_issues as etiquetas  # noqa: E402
import valida_ticket_issue as puerta  # noqa: E402

TAX = json.loads(TAXONOMIA.read_text(encoding="utf-8"))
CAMPOS_OK = {
    "writer": "qoderai[bot]",
    "verificador": "mog-verificador",
    "criterio": "python3 .github/scripts/test_valida_ticket_issue.py",
    "rutas": ".github/scripts/valida_ticket_issue.py",
    "repo_destino": "mog-corporation/mog-control-plane",
    "job": "j8851de79d515",
}


def cuerpo(**sobrescribe) -> str:
    """Cuerpo como lo renderiza un Issue Form: `### <titulo>` y el valor debajo."""
    valores = dict(CAMPOS_OK)
    valores.update(sobrescribe)
    partes = []
    for clave, titulo in TAX["formulario"].items():
        valor = valores.get(clave, "")
        partes.append("### %s\n\n%s\n" % (titulo, valor if valor else "_No response_"))
    return "\n".join(partes)


def valida(texto: str, tax: dict | None = None) -> dict:
    return puerta.valida(texto, tax or TAX)


class Positivo(unittest.TestCase):
    def test_issue_completo_se_acepta(self):
        r = valida(cuerpo())
        self.assertEqual(r["veredicto"], "ACEPTADO", r)
        self.assertEqual(r["causas"], [])

    def test_criterio_en_valla_de_codigo_se_acepta(self):
        r = valida(cuerpo(criterio="```shell\npython3 .github/scripts/mide_resenas.py --repo x/y --pr 1\n```"))
        self.assertEqual(r["veredicto"], "ACEPTADO", r)

    def test_varias_rutas_una_por_linea(self):
        r = valida(cuerpo(rutas=".github/scripts/a.py\ngovernance/b.json"))
        self.assertEqual(r["veredicto"], "ACEPTADO", r)


class Negativo(unittest.TestCase):
    def test_cada_campo_obligatorio_que_falta_se_rechaza_por_su_nombre(self):
        for clave in TAX["requeridos"]:
            with self.subTest(campo=clave):
                r = valida(cuerpo(**{clave: ""}))
                self.assertEqual(r["veredicto"], "RECHAZADO")
                self.assertIn("FALTA_" + clave.upper(), r["causas"])

    def test_cuerpo_vacio_rechaza_los_seis(self):
        r = valida("")
        self.assertEqual(r["veredicto"], "RECHAZADO")
        self.assertEqual(len(r["causas"]), len(TAX["requeridos"]))

    def test_criterio_sin_comando_no_vale(self):
        r = valida(cuerpo(criterio="Revisar a mano y quedar tranquilos"))
        self.assertIn("CRITERIO_NO_EJECUTABLE", r["causas"])

    def test_repo_destino_sin_owner_se_rechaza(self):
        r = valida(cuerpo(repo_destino="mog-universe"))
        self.assertIn("REPO_DESTINO_INVALIDO", r["causas"])

    def test_job_inventado_se_rechaza(self):
        for malo in ("NONE", "JOB-123", "j123"):
            with self.subTest(job=malo):
                self.assertIn("JOB_INVALIDO", valida(cuerpo(job=malo))["causas"])

    def test_rutas_sin_fichero_no_vale(self):
        r = valida(cuerpo(rutas="todo lo que haga falta"))
        self.assertIn("RUTAS_NO_RECONOCIBLES", r["causas"])

    def test_sin_respuesta_del_formulario_equivale_a_vacio(self):
        r = valida(cuerpo(writer="_No response_"))
        self.assertIn("FALTA_WRITER", r["causas"])

    def test_sin_respuesta_entre_backticks_tambien_es_vacio(self):
        # Caso real: alguien pega el marcador a mano y GitHub lo muestra con comillas.
        r = valida(cuerpo(writer="`_No response_`", rutas="```\n_No response_\n```"))
        self.assertIn("FALTA_WRITER", r["causas"])
        self.assertIn("FALTA_RUTAS", r["causas"])


class FailClosed(unittest.TestCase):
    def test_registro_ilegible_es_no_medido_y_nunca_acepta(self):
        with tempfile.TemporaryDirectory() as d:
            roto = Path(d) / "tax.json"
            roto.write_text("{no es json", encoding="utf-8")
            with self.assertRaises(puerta.NoMedido):
                puerta.carga_taxonomia(roto)

    def test_schema_ajeno_es_no_medido(self):
        with tempfile.TemporaryDirectory() as d:
            otro = Path(d) / "tax.json"
            otro.write_text(json.dumps({"schema": "otra.cosa"}), encoding="utf-8")
            with self.assertRaises(puerta.NoMedido):
                puerta.carga_taxonomia(otro)

    def test_requerido_sin_titulo_es_no_medido(self):
        mutada = json.loads(json.dumps(TAX))
        mutada["requeridos"] = mutada["requeridos"] + ["campo_fantasma"]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "tax.json"
            f.write_text(json.dumps(mutada), encoding="utf-8")
            with self.assertRaises(puerta.NoMedido):
                puerta.carga_taxonomia(f)

    def test_cli_sale_2_con_registro_ilegible(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "tax.json"
            f.write_text("[]", encoding="utf-8")
            c = Path(d) / "cuerpo.md"
            c.write_text(cuerpo(), encoding="utf-8")
            self.assertEqual(puerta.main(["--cuerpo", str(c), "--taxonomia", str(f)]), 2)

    def test_cli_sale_1_con_cuerpo_incompleto_y_0_con_completo(self):
        with tempfile.TemporaryDirectory() as d:
            incompleto = Path(d) / "malo.md"
            incompleto.write_text(cuerpo(writer=""), encoding="utf-8")
            self.assertEqual(puerta.main(["--cuerpo", str(incompleto), "--taxonomia", str(TAXONOMIA)]), 1)
            completo = Path(d) / "bueno.md"
            completo.write_text(cuerpo(), encoding="utf-8")
            self.assertEqual(puerta.main(["--cuerpo", str(completo), "--taxonomia", str(TAXONOMIA)]), 0)


class Mutante(unittest.TestCase):
    """El mutante: si el registro deja de exigir un campo, el banco lo detecta."""

    def test_quitar_un_requerido_del_registro_cambia_el_veredicto(self):
        mutada = json.loads(json.dumps(TAX))
        mutada["requeridos"] = [c for c in mutada["requeridos"] if c != "writer"]
        cuerpo_sin_writer = cuerpo(writer="")
        self.assertEqual(valida(cuerpo_sin_writer)["veredicto"], "RECHAZADO")
        self.assertEqual(valida(cuerpo_sin_writer, mutada)["veredicto"], "ACEPTADO",
                         "quitar 'writer' del registro no cambio nada: el campo no estaba gobernando")

    def test_requeridos_exactos_del_encargo(self):
        self.assertEqual(sorted(TAX["requeridos"]),
                         sorted(["writer", "verificador", "criterio", "rutas", "repo_destino", "job"]))

    def test_gastar_la_valla_en_una_clave_desactiva_el_criterio(self):
        mutada = json.loads(json.dumps(TAX))
        mutada["criterio_prefijos"] = []
        self.assertIn("CRITERIO_NO_EJECUTABLE", valida(cuerpo(), mutada)["causas"])


class SincroniaFormulario(unittest.TestCase):
    """El vocabulario no se hornea: formulario y registro tienen que decir lo mismo."""

    def setUp(self):
        self.form = yaml.safe_load(FORMULARIO.read_text(encoding="utf-8"))
        self.campos = {c.get("id"): c for c in self.form["body"] if c.get("type") != "markdown"}

    def test_titulos_del_formulario_igual_al_registro(self):
        for clave, titulo in TAX["formulario"].items():
            self.assertIn(clave, self.campos, "el formulario no tiene el campo %s" % clave)
            self.assertEqual(self.campos[clave]["attributes"]["label"], titulo)

    def test_los_seis_requeridos_son_obligatorios_en_el_formulario(self):
        for clave in TAX["requeridos"]:
            self.assertTrue(self.campos[clave]["validations"]["required"], "campo %s no obligatorio en el YAML" % clave)

    def test_opciones_de_area_prioridad_y_estado_salen_del_registro(self):
        for campo, seccion in (("area", "areas"), ("prioridad", "prioridad"), ("estado", "estado")):
            self.assertEqual(list(self.campos[campo]["attributes"]["options"]), list(TAX[seccion]))

    def test_un_solo_formulario_y_sin_issue_en_blanco(self):
        plantillas = sorted(p.name for p in (RAIZ / ".github" / "ISSUE_TEMPLATE").iterdir() if p.suffix in (".yml", ".md"))
        self.assertEqual(plantillas, ["config.yml", "ticket.yml"])
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertFalse(cfg["blank_issues_enabled"])

    def test_aviso_va_a_discussions_y_decision_a_acta(self):
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        urls = [c["url"] for c in cfg["contact_links"]]
        self.assertTrue(any("/discussions/new?category=announcements" in u for u in urls), urls)
        self.assertTrue(any("/discussions/new?category=general" in u for u in urls), urls)
        self.assertEqual(TAX["canal"]["aviso"]["destino"], "discussions")
        self.assertEqual(TAX["canal"]["decision"]["destino"], "acta")


class WorkflowReutilizable(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.texto = WORKFLOW.read_text(encoding="utf-8")
        self.job = self.wf["jobs"]["puerta"]

    def test_se_declara_como_workflow_call_con_pin_obligatorio(self):
        self.assertEqual(list(self.wf[True]), ["workflow_call"])
        entrada = self.wf[True]["workflow_call"]["inputs"]["bundle_sha"]
        self.assertTrue(entrada["required"])
        self.assertEqual(entrada["type"], "string")

    def test_no_declara_permissions_que_dejen_gh_sin_token(self):
        self.assertNotIn("permissions", self.wf, "el caller concede los permisos; declararlos aqui los recorta")

    def test_dispara_al_abrir_y_al_corregir_sin_barrer_el_backlog(self):
        self.assertIn("github.event.action == 'opened'", self.job["if"])
        self.assertIn("necesita-plantilla", self.job["if"])
        for peligro in ("--state", "--limit", "issues list"):
            self.assertNotIn(peligro, self.texto, "el gate no debe barrer el backlog: %s" % peligro)

    def test_exige_sha40_y_no_acepta_ramas(self):
        self.assertIn("[0-9a-f]{40}", self.texto)
        self.assertIn("bundle_sha", self.texto)

    def test_lee_validador_y_taxonomia_del_bundle_y_no_de_una_lista_horneada(self):
        self.assertIn("bundle/.github/scripts/valida_ticket_issue.py", self.texto)
        self.assertIn("bundle/.github/scripts/issue-taxonomy.active.json", self.texto)
        self.assertNotIn("FALTA_WRITER", self.texto)

    def test_el_cuerpo_entra_por_env_y_fichero(self):
        guarda = [s for s in self.job["steps"] if s.get("name") == "Guardar el cuerpo del issue"][0]
        self.assertIn("CUERPO", guarda["env"])
        self.assertNotIn("github.event.issue.body", guarda["run"])

    def test_falla_cerrado_comentando_etiquetando_y_saliendo_en_rojo(self):
        rechaza = [s for s in self.job["steps"] if s.get("name") == "Rechazar, comentar y marcar"][0]
        self.assertEqual(rechaza["if"].strip(), "steps.valida.outputs.rc != '0'")
        self.assertIn("--add-label", rechaza["run"])
        self.assertIn("--body-file", rechaza["run"])
        self.assertIn("exit 1", rechaza["run"])
        self.assertIn("TICKET_RECHAZADO", rechaza["run"])

    def test_no_medido_tambien_rechaza(self):
        # rc=2 (NO_MEDIDO) cae en `!= '0'`: lo no medido no autoriza.
        rechaza = [s for s in self.job["steps"] if s.get("name") == "Rechazar, comentar y marcar"][0]
        self.assertNotIn("rc == '2'", rechaza["if"])
        self.assertNotIn("rc != '0' &&", rechaza["if"])

    def test_solo_aceptado_retira_la_marca(self):
        acepta = [s for s in self.job["steps"] if s.get("name") == "Retirar la marca al quedar completo"][0]
        self.assertEqual(acepta["if"].strip(), "steps.valida.outputs.rc == '0'")
        self.assertIn("--remove-label", acepta["run"])


class Etiquetas(unittest.TestCase):
    def test_vocabulario_con_prefijos(self):
        d = etiquetas.etiquetas_deseadas(TAX)
        for nombre in ("area:motor-ci", "area:front", "prioridad:p0", "prioridad:p3",
                       "estado:en-curso", "estado:decision-pendiente", "necesita-plantilla"):
            self.assertIn(nombre, d)
        self.assertEqual(len(d), len(TAX["areas"]) + len(TAX["prioridad"]) + len(TAX["estado"]) + 1)

    def test_la_fan_out_no_retira_nada(self):
        self.assertEqual(TAX.get("retiradas"), [], "la fan-out es aditiva: retiradas tiene que estar vacia")
        self.assertNotIn("ANTI-DERIVA", etiquetas.etiquetas_deseadas(TAX))

    def test_modo_aditivo_ignora_las_retiradas_del_registro(self):
        con_retirada = json.loads(json.dumps(TAX))
        con_retirada["retiradas"] = ["area:front"]
        self.assertIn("area:front", etiquetas.etiquetas_deseadas(con_retirada, retira=False))
        self.assertNotIn("area:front", etiquetas.etiquetas_deseadas(con_retirada, retira=True))

    def test_el_sincronizador_no_tiene_ruta_de_borrado(self):
        fuente = (RAIZ / ".github" / "scripts" / "sincroniza_etiquetas_issues.py").read_text(encoding="utf-8")
        self.assertNotIn("label delete", fuente)
        self.assertNotIn('"delete"', fuente)

    def test_colores_y_descripciones_vienen_del_registro(self):
        d = etiquetas.etiquetas_deseadas(TAX)
        for nombre, meta in d.items():
            self.assertRegex(meta["color"], r"^[0-9A-F]{6}$", nombre)
            self.assertTrue(meta["description"], nombre)

    def test_deriva_detecta_faltantes_y_desalineadas(self):
        deseado = etiquetas.etiquetas_deseadas(TAX)
        sin_nada = etiquetas.deriva(deseado, {})
        self.assertEqual(len(sin_nada["faltan"]), len(deseado))
        actual = {n: dict(m) for n, m in deseado.items()}
        actual["area:motor-ci"]["color"] = "000000"
        del actual["estado:en-curso"]
        d = etiquetas.deriva(deseado, actual)
        self.assertEqual(d["faltan"], ["estado:en-curso"])
        self.assertEqual(d["desalineadas"], ["area:motor-ci"])

    def test_aplicar_es_idempotente(self):
        # La segunda pasada no encuentra deriva: el vocabulario no se reescribe en cada corrida.
        deseado = etiquetas.etiquetas_deseadas(TAX)
        self.assertEqual(etiquetas.deriva(deseado, deseado), {"faltan": [], "desalineadas": []})

    def test_registro_ilegible_es_no_medido(self):
        with tempfile.TemporaryDirectory() as t:
            roto = Path(t) / "tax.json"
            roto.write_text("{", encoding="utf-8")
            with self.assertRaises(etiquetas.NoMedido):
                etiquetas.carga(roto)


class Idempotencia(unittest.TestCase):
    def test_mismo_cuerpo_mismo_veredicto(self):
        a, b = cuerpo(), cuerpo()
        self.assertEqual(valida(a), valida(b))

    def test_ruido_de_espacios_y_backticks_no_cambia_el_veredicto(self):
        limpio = cuerpo()
        sucio = "\n\n" + limpio.replace("### Writer\n\nqoderai[bot]", "### Writer\n\n`qoderai[bot]` \n") + "\n\n"
        self.assertEqual(valida(limpio)["veredicto"], valida(sucio)["veredicto"])

    def test_comentario_de_rechazo_es_determinista(self):
        causas = ["FALTA_WRITER", "CRITERIO_NO_EJECUTABLE"]
        self.assertEqual(puerta.cuerpo_rechazo(causas, "necesita-plantilla"),
                         puerta.cuerpo_rechazo(causas, "necesita-plantilla"))
        self.assertIn("FALTA_WRITER", puerta.cuerpo_rechazo(causas, "necesita-plantilla"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
