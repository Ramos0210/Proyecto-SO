# HEADSHOT ARENA

Juego multijugador en red hecho con **Python, Pygame y sockets TCP**. Servidor con SQLite
(cuentas, estadísticas y ranking) y cliente gráfico con stickmans.

## Modos de juego (salas independientes)

| Modo | Reglas |
|------|--------|
| **Only 200** | Solo cuentan los **headshots** (1 tiro = muerte; el cuerpo no recibe daño). Cada kill seguida **agranda tu cabeza** (más fácil de impactar) y te da multiplicadores de **velocidad, salto, cadencia y puntos**. Morir reinicia la racha. Gana el primero en llegar a **200 puntos**. |
| **PvP clásico** | Todas las armas: pistola, subfusil, escopeta, francotirador, cuchillo, espada, bate y hacha. Vida 100, headshot x2 (melee x1.5). Primero a 200 puntos. |
| **Cuerpo a cuerpo** | Solo cuchillo, espada, bate y hacha. Primero a 100 puntos. |

Puntos: Only 200 = 10 × multiplicador de racha (x1 → x5). PvP/Melee = 10 por kill (+5 si es headshot).

## Instalación y ejecución

```bash
pip install -r requirements.txt

# 1) Servidor (puerto 5555 por defecto)
python server/server.py            # opciones: --host --port --db --target

# 2) Cliente (uno por jugador)
python client/client.py            # o: python client/client.py 192.168.1.20:5555
```

Para jugar en red local, abre el cliente con la IP del equipo donde corre el servidor.
En internet, abre/redirige el puerto TCP 5555 en el router.

## Controles

| Acción | Tecla |
|--------|-------|
| Mover | A / D o flechas |
| Saltar | W, Espacio o ↑ |
| Atravesar plataforma | S o ↓ |
| Apuntar | Mouse |
| Disparar / atacar | Click izquierdo (mantener) |
| Cambiar arma | 1–8 o rueda del mouse |
| Volver al menú | ESC |

## Arquitectura

```
multijugador_juego/
├── server/server.py   # sockets + hilos, lógica de partida autoritativa, SQLite
├── client/client.py   # Pygame: login, menú, ranking y partida
├── requirements.txt
└── README.md
```

* **Protocolo**: JSON, un mensaje por línea. Cliente → servidor: `register`, `login`, `modes`,
  `join`, `leave`, `input`, `ranking`, `stats`. Servidor → cliente: `auth`, `modes`, `joined`,
  `state` (30 Hz), `ranking`, `stats`.
* **Servidor autoritativo**: física a 60 Hz; el cliente solo envía teclas y ángulo de apuntado.
  Los disparos son instantáneos (hitscan) con detección contra cabeza (círculo) y cuerpo (caja).
* **Seguridad básica**: contraseñas con PBKDF2-SHA256 + sal, consultas SQL parametrizadas,
  una sesión por usuario, límite de tamaño de mensaje.
* **Base de datos** (`server/database.db`, se crea sola): tablas `users` y `mode_stats`
  (victorias, puntos, kills, headshots y mejor racha por modo). El ranking se puede ver global o por modo.

## Ajustes rápidos (server.py)

* `MODES[...]["target"]`: puntos para ganar. `WEAPONS`: daño, cadencia, alcance, retroceso.
* `HEAD_BASE`, `HEAD_GROW`, `HEAD_MAX`: tamaño de cabeza y crecimiento por racha.
* `speed_mult`, `jump_mult`, `fire_mult`, `score_mult`: multiplicadores de racha.
* `PLATFORMS` y `SPAWNS`: diseño del mapa.
