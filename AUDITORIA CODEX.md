# AUDITORIA CODEX

## Resumen Ejecutivo

ILUMINATY tiene una propuesta de producto excepcionalmente ambiciosa y valiosa: no solo darle ojos a una IA, sino también manos, contexto, memoria operativa y capacidad de actuar sobre el entorno real del usuario. La visión es potente, diferenciada y comercialmente atractiva. El proyecto ya contiene una base técnica relevante, una arquitectura conceptual clara por capas y un posicionamiento de producto fuerte.

La conclusión principal de esta auditoría es la siguiente:

- La visión del producto es fuerte.
- La base técnica es prometedora.
- El mayor riesgo actual no es la falta de features, sino la falta de consolidación estructural.
- El sistema necesita endurecer seguridad, unificar contratos internos y ordenar su arquitectura antes de seguir creciendo agresivamente.

Hoy ILUMINATY se percibe como un prototipo avanzado con potencial real de plataforma. Para dar el salto a un producto robusto, confiable y escalable, hace falta corregir inconsistencias entre promesa e implementación, centralizar la gobernanza del sistema y formalizar el modelo de ejecución segura para agentes.

En una frase:

**ILUMINATY ya tiene la ambición correcta; ahora necesita una base operativa igual de fuerte que su visión.**

---

## Diagnóstico General

### Lo más fuerte del proyecto

- La propuesta "AI that can see and act" es clara, memorable y diferencial.
- La división conceptual por capas está bien pensada.
- La mezcla de visión, OCR, UI tree, browser, terminal, filesystem y acciones OS crea una base muy poderosa.
- El enfoque de resolvedor en cascada es una idea sólida de producto y de sistema.
- La narrativa del proyecto está bien construida y transmite dirección.
- Hay suficiente volumen de implementación como para considerar que esto ya supera una demo simple.

### Lo más delicado del proyecto

- El sistema expone superficies de alto riesgo sin una política unificada de seguridad.
- Existen inconsistencias importantes entre README, servidor, licensing y MCP.
- El código está modularizado, pero el acoplamiento real sigue alto.
- La seguridad prometida no siempre coincide con la seguridad aplicada.
- La base de testing no parece proporcional al riesgo operativo del producto.
- La app desktop contiene señales de bypass de desarrollo incompatibles con una distribución seria.

---

## Hallazgos Críticos

## 1. Seguridad del producto insuficiente para el poder que expone

ILUMINATY no es una app común. Está diseñándose como una capa operativa que puede:

- ver la pantalla,
- interpretar UI,
- escribir teclado,
- mover mouse,
- leer y escribir archivos,
- ejecutar terminal,
- operar browser,
- interactuar con ventanas y procesos.

Ese nivel de poder requiere un modelo de seguridad mucho más estricto que el que suele necesitar una API local tradicional.

### Problemas detectados

- Existe bypass de desarrollo embebido en la app desktop.
- Se usan credenciales o llaves privilegiadas hardcodeadas.
- Muchas acciones sensibles pueden invocarse directo por endpoint.
- El `SafetySystem` no gobierna uniformemente todas las acciones directas.
- La autonomía del agente no parece ser la puerta obligatoria para toda operación riesgosa.
- El modelo actual depende demasiado de "tener API key" en vez de permisos finos.

### Impacto

- Una fuga de key o bug de autorización puede dar control amplio del entorno.
- El usuario puede asumir que el sistema está más blindado de lo que realmente está.
- La confianza del producto puede quebrarse si una integración actúa por fuera del flujo esperado.

### Recomendación

Toda acción sensible debe pasar por una misma política central, sin excepciones.

Esa política debe resolver:

- si la acción está permitida,
- bajo qué plan está disponible,
- qué nivel de autonomía exige,
- si requiere confirmación,
- si requiere contexto válido,
- si debe registrarse con auditoría obligatoria,
- si puede ejecutarse según app, dominio, ventana o path.

---

## 2. Deriva entre promesa de producto e implementación real

La promesa de "zero disk" es muy fuerte comercialmente, pero hoy no está representada con precisión.

### Problemas detectados

- Hay auditoría persistente en SQLite.
- Hay cache local de licencias.
- MCP genera archivo de debug.
- El mensaje "nada se guarda" no es exacto en todas las rutas del sistema.

### Impacto

- Riesgo reputacional.
- Riesgo de soporte.
- Riesgo de generar una expectativa de privacidad que no coincide con el comportamiento real.

### Recomendación

Reformular el modelo de privacidad con precisión:

- `Ephemeral visual mode`: frames y buffer solo en RAM.
- `Audit mode`: persistencia de metadata operativa.
- `Compliance mode`: trazabilidad extendida.
- `Privacy hardened mode`: sin cache, sin logs, sin audit persistente.

La comunicación del producto debe ser exacta. En seguridad y privacidad, la precisión importa más que el marketing.

---

## 3. Modelo de licencias y capacidades desalineado

El sistema de planes, herramientas y endpoints necesita consolidación.

### Problemas detectados

- Rutas declaradas en licensing no coinciden totalmente con rutas reales del servidor.
- Hay múltiples catálogos manuales de endpoints y tools.
- Los permisos del MCP y del server no parecen salir de una misma fuente de verdad.
- El enfoque "permit unknown endpoints" es peligroso.

### Impacto

- Nuevos endpoints podrían quedar abiertos accidentalmente.
- Cambios futuros rompen consistencia.
- El producto se vuelve difícil de auditar y mantener.

### Recomendación

Crear un catálogo único declarativo de capacidades.

Ejemplo conceptual:

- `capability_id`
- `transport` (`http`, `mcp`, `desktop`)
- `plan_required`
- `permission_scope`
- `risk_level`
- `confirmation_mode`
- `audit_required`
- `feature_flag`

Luego:

- FastAPI deriva permisos desde ese catálogo.
- MCP deriva tools desde ese catálogo.
- Desktop deriva UI/availability desde ese catálogo.
- README puede derivar documentación visible desde ese catálogo.

---

## 4. Acoplamiento excesivo en la capa de servidor

Aunque el repositorio tiene muchos módulos, la capa central sigue tomando demasiadas decisiones manuales.

### Problemas detectados

- `server.py` concentra demasiada orquestación.
- Las reglas de negocio están repartidas.
- El crecimiento del sistema depende de sincronización manual entre archivos.
- Hay demasiado conocimiento implícito entre módulos.

### Impacto

- El sistema escala en features más rápido que en claridad.
- Cada mejora aumenta el riesgo de desalineación.
- Refactorizar se vuelve más costoso con el tiempo.

### Recomendación

Reorganizar el proyecto por dominios funcionales y no solo por módulos aislados.

---

## Bugs y Riesgos Detectados

## Bugs críticos

### 1. Bypass de autenticación/desarrollo en cliente desktop

Esto es un hallazgo severo.

- Hay lógica para aceptar credenciales de desarrollo si el auth server no responde.
- Se entrega una llave privilegiada desde el cliente.

Esto no debe existir en una build distribuible.

### 2. Puerta de seguridad inconsistente entre `agent/do` y endpoints directos

- `agent/do` aplica validaciones más ricas.
- Muchas rutas directas ejecutan acciones sin pasar por la misma política.

Esto genera dos modelos de seguridad distintos dentro del mismo sistema.

### 3. Licencias potencialmente bypassables por deriva de rutas

- Si cambia una ruta y licensing no se actualiza, el endpoint puede quedar sin control real.
- La política permisiva para endpoints desconocidos lo agrava.

### 4. Exposición excesiva de ejecución arbitraria

- Terminal, browser eval, git y filesystem combinados forman una superficie muy poderosa.
- Si no están gobernados por scopes finos, el riesgo es muy alto.

## Bugs medios

### 5. Inconsistencia de versiones

- `pyproject` y banner/API no coinciden.
- Esto rompe claridad de release, soporte y debugging.

### 6. Promesas de producto no exactas

- "Zero disk" no es completamente cierto.
- Eso puede convertirse en bug de confianza.

### 7. Soporte cross-platform desigual

- Algunas piezas parecen mucho más maduras en Windows que en macOS/Linux.
- Algunas rutas de soporte están en estado parcial o aspiracional.

## Bugs de diseño/arquitectura

### 8. Falta de una fuente única de verdad

- Planes.
- Tools.
- Endpoints.
- Versionado.
- Claims del producto.
- Riesgos por acción.

Todo esto debe salir de una misma metadata central.

### 9. Testing insuficiente para el nivel de riesgo

No parece haber cobertura suficiente para:

- policy enforcement,
- licensing,
- sandboxing,
- destructive actions,
- consistency contracts,
- regression safety.

---

## Reordenamiento de Código Recomendado

## Objetivo

Pasar de una organización "muchos módulos con coordinación manual" a una arquitectura basada en dominios, contratos y políticas centralizadas.

## Estructura sugerida

```text
iluminaty/
  core/
    config.py
    version.py
    registry.py
    errors.py
    types.py

  policy/
    capabilities.py
    plans.py
    scopes.py
    risk.py
    enforcement.py
    confirmation.py

  perception/
    capture.py
    ring_buffer.py
    vision.py
    smart_diff.py
    audio.py
    context.py
    monitors.py
    fusion.py

  actuation/
    actions.py
    windows.py
    clipboard.py
    process_mgr.py
    browser.py
    terminal.py
    filesystem.py
    vscode.py
    git_ops.py

  intelligence/
    intent.py
    resolver.py
    planner.py
    verifier.py
    recovery.py

  safety/
    safety.py
    autonomy.py
    audit.py
    secrets.py
    watchdog.py

  licensing/
    manager.py
    cache.py
    gating.py

  transports/
    http/
      routes_vision.py
      routes_actions.py
      routes_system.py
      routes_admin.py
    mcp/
      server.py
      tools.py
    desktop/
      bridge.py

  integrations/
    adapters.py
    plugin_system.py
    collab.py
    relay.py
```

## Qué ganarías con ese reordenamiento

- Menos acoplamiento accidental.
- Mejor mantenibilidad.
- Seguridad más gobernable.
- Más facilidad para agregar apps, plugins y capabilities.
- Mejor testing por dominio.

---

## Nuevo Modelo de Seguridad Propuesto

## 1. Capability Registry

Cada capacidad del sistema debe existir como una entidad formal.

Ejemplos:

- `vision.snapshot`
- `vision.ocr`
- `actions.click`
- `actions.type`
- `browser.navigate`
- `browser.eval`
- `terminal.exec`
- `filesystem.read`
- `filesystem.write`
- `git.commit`
- `windows.focus`

Cada una debe tener:

- plan requerido,
- nivel de riesgo,
- scope permitido,
- modo de confirmación,
- si requiere audit,
- si es reversible,
- si se puede ejecutar en `AUTO`.

## 2. Deny by default

Ninguna capability nueva debe quedar expuesta automáticamente.

Todo capability nuevo debe declararse de forma explícita.

## 3. Scopes finos

No basta con una API key global.

Debe haber scopes como:

- `vision.read`
- `audio.read`
- `actions.mouse`
- `actions.keyboard`
- `browser.read`
- `browser.write`
- `terminal.exec`
- `filesystem.read`
- `filesystem.write`
- `git.read`
- `git.write`

## 4. Restricciones contextuales

Poder permitir o negar según:

- aplicación,
- ventana,
- dominio web,
- directorio,
- tipo de acción,
- horario,
- agente/proveedor.

## 5. Confirmación contextual

No todo requiere la misma fricción.

Ejemplos:

- `click` en UI conocida: bajo riesgo.
- `type_text` en campo no sensible: riesgo medio.
- `terminal.exec`: alto riesgo.
- `git push`: alto riesgo.
- `files.delete`: alto riesgo.
- `browser submit/purchase/send`: muy alto riesgo.

---

## Modelo de Flujo Recomendado

La ejecución ideal de una acción debería seguir este pipeline:

1. Percepción.
2. Clasificación de intención.
3. Resolución de capability requerida.
4. Policy check.
5. Preview/dry-run opcional.
6. Confirmación si aplica.
7. Ejecución.
8. Verificación.
9. Recovery o rollback.
10. Audit y evidencia.

Ese pipeline debe ser transversal a todos los transportes:

- API HTTP,
- MCP,
- Desktop app,
- futuros SDKs.

---

## Features Prioritarios de Producto

## 1. Policy Engine Visual

Este puede ser uno de los features más importantes del proyecto.

Permitir configurar reglas como:

- "Esta IA puede ver pantalla pero no usar terminal."
- "Solo puede actuar dentro de VS Code."
- "Puede leer archivos, pero no escribir."
- "No puede interactuar con apps bancarias."
- "No puede enviar formularios sin confirmación."
- "Solo puede operar dentro del workspace actual."

### Por qué importa

- Convierte poder técnico en confianza de producto.
- Hace el sistema adoptable para usuarios reales.
- Te diferencia de soluciones más "demo" o más peligrosas.

---

## 2. Session Sandboxes

Perfiles de sesión listos para usar:

- `Observe Only`
- `Suggest Only`
- `Safe Assist`
- `Developer Operator`
- `Browser Assistant`
- `Workspace-Limited Agent`

### Valor

- Reduce miedo.
- Facilita onboarding.
- Hace más claro lo que la IA puede y no puede hacer.

---

## 3. Action Preview / Dry Run

Antes de actuar, el sistema muestra:

- qué entendió,
- qué acción piensa ejecutar,
- sobre qué app/ventana,
- con qué método,
- qué espera verificar.

### Valor

- Aumenta confianza.
- Reduce errores.
- Convierte el sistema en algo más explicable.

---

## 4. Verification Engine Avanzado

La verificación debe convertirse en una gran fortaleza del producto.

No basta con ejecutar; hay que confirmar.

### Modos de verificación

- visual,
- OCR,
- DOM,
- UI tree,
- filesystem,
- terminal output,
- process/window state.

### Valor

- Diferenciación real.
- Menos acciones fantasmas.
- Más robustez de agente.

---

## 5. Intelligent App Profiles

ILUMINATY puede crecer muchísimo si deja de pensar solo en acciones genéricas y empieza a pensar en perfiles por aplicación.

Ejemplos:

- VS Code profile
- Chrome profile
- Terminal profile
- Slack profile
- Notion profile
- Figma profile
- Excel profile

Cada perfil puede exponer:

- operaciones comunes,
- métodos seguros,
- validadores específicos,
- scopes por defecto.

---

## Features Visionarios Recomendados

Esta sección enfatiza las oportunidades más grandes del proyecto.

## 1. Teach by Demonstration

### Idea

El usuario realiza una tarea manual una vez y ILUMINATY aprende el flujo:

- qué ventana usó,
- qué botones tocó,
- qué textos escribió,
- qué validaciones ocurrieron,
- qué condiciones de éxito existían.

Luego la IA puede:

- repetir la tarea,
- adaptarla,
- convertirla en workflow,
- pedir confirmación solo en puntos ambiguos.

### Potencial

Esto puede transformar ILUMINATY de "agente que improvisa acciones" a "agente que aprende operaciones reales del usuario".

### Casos

- publicar contenido,
- abrir proyecto y correr tests,
- preparar entorno de trabajo,
- responder tickets repetitivos,
- abrir dashboards y recolectar métricas.

### Valor estratégico

Muy alto. Reduce dependencia de prompts perfectos y convierte comportamiento humano en automatización reusable.

---

## 2. Watch Mode / Guardian Mode

### Idea

ILUMINATY observa el entorno y solo interviene cuando detecta una condición importante.

Ejemplos:

- build roto,
- error modal,
- test suite fallida,
- app congelada,
- terminal con stack trace,
- mensaje crítico en Slack,
- alerta de seguridad,
- formulario incompleto.

### Modos posibles

- solo alertar,
- sugerir acción,
- auto-remediar si está dentro de policy,
- pedir confirmación con plan sugerido.

### Valor estratégico

Convierte ILUMINATY en una capa de vigilancia activa, no solo en una herramienta reactiva.

---

## 3. Multi-Agent Workbench

### Idea

Separar roles de agentes:

- agente observador,
- agente planificador,
- agente ejecutor,
- agente verificador,
- agente auditor.

### Beneficios

- mejor trazabilidad,
- menos errores,
- mejor explicabilidad,
- mayor seguridad,
- más facilidad para rollback.

### Visión

No una IA única haciendo todo, sino una pequeña orquesta de agentes con responsabilidades claras.

### Valor estratégico

Altísimo si apuntas a usuarios avanzados, equipos técnicos o flujos críticos.

---

## 4. Declarative Workflows

### Idea

Convertir tareas comunes en workflows formales y guardables.

Ejemplos:

- `debug_local_project`
- `review_repo_changes`
- `prepare_daily_report`
- `open_workspace_and_resume`
- `collect_browser_research`

Cada workflow podría definir:

- pasos,
- permisos requeridos,
- acciones permitidas,
- verificaciones,
- rollback,
- intervención humana esperada.

### Valor estratégico

Esto mueve el producto de herramienta a plataforma.

---

## 5. Secret-Aware Computer Use

### Idea

El sistema detecta contextos sensibles y cambia automáticamente su comportamiento.

Ejemplos:

- password fields,
- secrets managers,
- cloud consoles,
- billing pages,
- bank apps,
- auth flows,
- API keys en pantalla o archivos.

### Comportamiento

- bloquear captura detallada,
- impedir escritura automática,
- ocultar OCR,
- deshabilitar copy/read,
- pedir confirmación reforzada.

### Valor estratégico

Es uno de los mayores diferenciadores posibles en computer use seguro.

---

## 6. Semantic UI Memory

### Idea

ILUMINATY recuerda semánticamente interfaces ya vistas:

- dónde suele estar un botón,
- qué label tiene un campo,
- qué app usa qué flujo,
- qué ventanas aparecen antes de cierto paso,
- qué confirmaciones son normales.

### Resultado

La IA deja de operar como si todo fuera nuevo cada vez.

### Valor estratégico

Hace al agente más rápido, más estable y más humano en su comportamiento.

---

## 7. Replay, Evidence and Time Travel

### Idea

Cada operación importante produce una evidencia navegable:

- qué vio,
- qué interpretó,
- qué acción eligió,
- qué ejecutó,
- qué verificó,
- qué falló,
- qué intentó después.

### Valor

- debugging,
- soporte,
- confianza,
- compliance,
- entrenamiento del sistema.

---

## Roadmap Recomendado

## Fase 1. Endurecimiento inmediato

### Objetivo

Cerrar riesgos severos antes de crecer más.

### Acciones

- eliminar bypass dev del cliente distribuible,
- eliminar llaves privilegiadas embebidas,
- bloquear exposición permisiva de endpoints desconocidos,
- unificar política de autorización,
- hacer pasar terminal/filesystem/browser/git por policy central,
- revisar claims de privacidad.

## Fase 2. Consolidación arquitectónica

### Objetivo

Reducir deriva y mejorar mantenibilidad.

### Acciones

- crear capability registry,
- separar server por dominios,
- generar disponibilidad de tools/endpoints desde metadata,
- centralizar versiones y feature claims.

## Fase 3. Seguridad operativa y UX de confianza

### Objetivo

Hacer que el producto sea poderoso pero gobernable.

### Acciones

- policy engine visual,
- session sandboxes,
- preview de acciones,
- confirmación contextual,
- audit y evidencia mejorados.

## Fase 4. Diferenciación fuerte

### Objetivo

Construir features difíciles de copiar.

### Acciones

- teach by demonstration,
- watch mode,
- semantic UI memory,
- declarative workflows,
- multi-agent workbench.

---

## Testing Recomendado

## Prioridades

- tests unitarios para `filesystem`, `licensing`, `safety`, `autonomy`, `resolver`,
- contract tests para endpoints y MCP,
- tests de consistencia de capability registry,
- tests de deny-by-default,
- tests de destructive actions,
- tests de verificación post-acción,
- tests end-to-end con mocks de OS/browser.

## Meta

Tener un sistema donde agregar un nuevo endpoint o nueva tool sin declararla explícitamente provoque fallo de test.

---

## Recomendaciones Finales

## Qué no hacer ahora

- No seguir agregando demasiados endpoints sin capability registry.
- No confiar en que README/manuales sigan sincronizados a mano.
- No distribuir builds con bypass de auth/dev.
- No vender "zero disk" sin matices precisos.

## Qué sí hacer ahora

- Endurecer seguridad.
- Unificar políticas.
- Reordenar arquitectura.
- Reducir deriva interna.
- Construir confianza de ejecución.

---

## Conclusión Final

ILUMINATY tiene una de las ideas más interesantes del repositorio: no solo ver el computador, sino operarlo con una IA de manera útil, segura y gobernada. Ese es un espacio con muchísimo potencial.

Pero precisamente porque el poder del sistema es tan alto, el estándar técnico debe subir al mismo nivel que la visión.

La mejor oportunidad del proyecto no está solo en "automatizar clicks". Está en convertirse en una **capa operativa confiable para agentes**, donde percepción, política, ejecución, verificación y recuperación formen un solo sistema.

Si se ejecuta bien, ILUMINATY puede evolucionar desde:

- una herramienta de computer use,

hacia:

- una plataforma de agentes operativos con memoria, policy, workflows y ejecución verificable.

Ese camino sí es ambicioso.
Y sí vale la pena.

