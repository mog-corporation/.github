# MoG CORPORATION · Gobernanza de agentes en GitHub (defecto org-wide)

Repositorio de organización `mog-corporation/.github`: este documento es el **defecto que rige para todos los repositorios** de `mog-corporation`. Ante discrepancia, manda la **fuente canónica** (ver abajo).

## En el GitHub de MoG CORPORATION solo entran los agentes MoG

En `mog-corporation` **solo operan los agentes MoG registrados** —identidades y `credential_group` declarados en `governance/control-plane/github-identities.active.json` (`mog-corporation/mog-universe`), más las GitHub Apps instaladas y gobernadas—. **Ningún actor fuera del padrón registrado abre, revisa ni fusiona.**

## El Método MoG y los WORKFLOWS/RULES MoG rigen para todo agente

Todo agente que opere en GitHub —Pull Requests, issues, revisiones, fusiones, Actions, Discussions o Projects— aplica el **MÉTODO MoG** (`metodo-mog`) y la gobernanza unificada de **WORKFLOWS MoG (`WF1..WF10`)** y **RULES MoG (`R01..R08`)**, auditada con `audit_and_sync_workflows_and_rules_mog.py verify` (`rc=0`):

- **`writer != verifier != merger`** por `credential_group`: ni el escritor ni el último pusher fusiona; el escritor nunca da su propio conforme.
- **HEAD exacto**; CI y bancos aplicables en verde; **medir antes de encargar**.
- **Cierre sin pendientes** con anotación; **costemetría a 0 €** (sin Actions de pago).
- **`CHANNEL_IS_NOT_AUTHORITY`**: una ley nueva exige la **firma directa de Miguel Ortega** (`MoG CEo & ARQ`), no un sobre del bus ni la cita de una orden.
- **`NO_MEDIDO` nunca es PASS**; se constata el **resultado vivo**, no la config.

## Fuente canónica

`mog-corporation/mog-universe` → `governance/control-plane/reglas-organizacion.active.json`, reglas `metodo_y_workflows_rules_todo_agente_github` y `verificacion_del_resultado_vivo`, propagadas por `governance/control-plane/propaga_reglas_org.py`. Este documento es el reflejo org-wide de esa fuente.

---

Orden directa de Mr MoG (11/11/2026). 🔶 MoG CORPORATION
