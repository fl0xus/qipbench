# Yasol-Service

Docker-Container mit [Yasol](https://github.com/MichaelHartisch/Yasol) (gebaut gegen
[HiGHS](https://github.com/ERGO-Code/HiGHS)) und einer HTTP-API mit zwei Endpoints:

| Endpoint | Eingabe | Zweck |
|---|---|---|
| `POST /solve/qlp` | `.qlp`-Datei (multipart) | Instanz lösen, Lösung als JSON zurückgeben |
| `POST /solve/julia` | Julia-Code (JSON) | Modell über [YasolSolver.jl](https://github.com/MichaelHartisch/YasolSolver.jl) bauen und lösen |

Dazu `GET /health`, `GET /examples` und `GET /examples/{name}` (die mit Yasol
ausgelieferten Beispielinstanzen). Interaktive Doku unter `/docs`.

## Bauen und starten

```bash
docker build -t yasol-service .
```

```bash
docker run -d --name yasol -p 127.0.0.1:8010:8000 yasol-service
```

Oder per Compose: `docker compose up -d` (Port 8000).

**Sicherheitshinweis:** `/solve/julia` führt beliebigen Code aus. Der Prozess läuft
unprivilegiert in einem Wegwerf-Verzeichnis, aber das ist Prozessisolation, keine
Sandbox. Nicht ins offene Netz hängen — deshalb im Beispiel oben `127.0.0.1`.

## `POST /solve/qlp`

```bash
curl -X POST http://127.0.0.1:8010/solve/qlp \
    -F "file=@knapsack.qlp" \
    -F "time_limit=60" \
    -F "options=--isSimplyRestricted=1"
```

Antwort:

```json
{
  "status": "OPTIMAL",
  "objective_value": 863.0,
  "variables": [{"name": "x_0_0", "index": "0", "value": 1.0, "block": 1}, "..."],
  "solution": {"gap": 8.63e14, "dual_bound": 863.0, "runtime_seconds": 4.877, "raw": "<XML>"},
  "exit_code": 0,
  "timed_out": false,
  "duration_seconds": 6.8,
  "command": ["/usr/local/bin/yasol", "knapsack.qlp", "--outputFile=1", "..."],
  "stdout": "...", "stderr": "..."
}
```

`options` akzeptiert nur `--name=<ganzzahl>` aus einer Allowlist. Grund: Yasols
Argument-Schleife ignoriert unbekannte Flags stillschweigend — ein Tippfehler würde
sonst unbemerkt wirkungslos bleiben. Die Allowlist macht daraus einen 400er.

## `POST /solve/julia`

```bash
curl -X POST http://127.0.0.1:8010/solve/julia \
    -H 'Content-Type: application/json' \
    -d '{"code": "using JuMP, YasolSolver\n...", "timeout": 120}'
```

Optional `files` (`{"name": "inhalt"}`), um zusätzliche Dateien ins Arbeitsverzeichnis
zu legen. Ein vollständiges Beispiel liegt in [examples/moore_bard.jl](examples/moore_bard.jl).

Im Arbeitsverzeichnis liegt bereits eine `Yasol.ini`, und `ENV["YASOL_BIN"]` zeigt auf
das Solver-Binary — beides braucht YasolSolver.jl:

```julia
set_optimizer_attribute(model, "solver path", ENV["YASOL_BIN"])
```

Antwort enthält `stdout`, `stderr`, `exit_code`, `timed_out`, alle erzeugten Dateien
(`artifacts`) und die geparsten Lösungsdateien (`solutions`).

## Fallstrick: Absturz bei universellem erstem Block mit Zielfunktion

Steht im **ersten** Variablenblock eine universelle (`ALL`) Variable und ist die
Zielfunktion **nicht leer**, bricht Yasol ab:

```
terminate called after throwing an instance of 'utils::QlpSolverException'
  what(): QuantifiedProblem::getVar(unsigned int) --> Index Exception i: 4294967295
```

Eingegrenzt am 30.07.2026:

| ALL-Block | Zielfunktion | Ergebnis |
|---|---|---|
| zuerst | nicht leer | Absturz |
| zuerst | nicht leer, ohne die universelle Variable | Absturz |
| zuerst | leer | läuft |
| in der Mitte | nicht leer | läuft |
| zuletzt | nicht leer | läuft |

Ob die universelle Variable in der Zielfunktion auftaucht, spielt keine Rolle — allein
ihre Position im ersten Block zählt. `4294967295` ist `(unsigned)-1`, also ein
fehlgeschlagener Variablen-Lookup.

Betroffen ist damit die Klasse der Min-Max-Probleme, bei denen der Gegenspieler zuerst
zieht. Der Service fängt den Absturz sauber ab (`execution_error`, kein Hänger), aber
Instanzen dieser Form sind mit diesem Solver-Stand nicht lösbar und gehören aus einem
Benchmark-Datensatz aussortiert.

## Statuswerte

Yasol kennt sechs Status (`Yasol.cpp`, Status-Dispatch). Der Service normalisiert zwei
davon; die Rohausgabe bleibt in `stdout` einsehbar.

| Yasol | Service meldet | Bedeutung |
|---|---|---|
| `OPTIMAL` | `OPTIMAL` | bewiesenes Optimum |
| `OPTIMAL` + Sentinel-Zielwert ±2^61 | **`INFEASIBLE`** | keine Lösung (siehe unten) |
| `UNSAT` | **`INFEASIBLE`** | keine Lösung, ohne Suche erkannt |
| `FEASIBLE` | `FEASIBLE` | zulässig; reines Feasibility-Problem ohne Zielwert |
| `INCUMBENT` | `INCUMBENT` | Lösung gefunden, Optimalität **nicht** bewiesen (Zeitlimit) |
| `UNKNOWN` | `UNKNOWN` | abgebrochen, Zulässigkeit ungeklärt |
| `ERROR` | `ERROR` | interner Fehler oder Parse-Problem |

Die beiden Normalisierungen sind nötig, weil Yasol „keine Lösung" auf zwei Wegen
erreicht und unterschiedlich benennt. Ohne Zusammenführung hinge es vom Codepfad ab, ob
eine korrekt unlösbare Instanz erkannt wird.

**`INCUMBENT` ist kein Optimum.** Wer Zielwerte als Referenz verwendet, muss diesen
Status ausschließen — der Wert kann suboptimal sein.

### Der Sentinel-Fall im Detail

Gibt es keine Lösung, schreibt Yasol keine `.sol`-Datei, gibt aber trotzdem
`Solution Status: OPTIMAL` aus — zusammen mit einem Sentinel-Zielwert von ±2^61
(`yInterface.cc`: `defineNegativeInfinity(-(1<<61))`), also `-2.30584e+18`. Ungefiltert
liest sich das wie eine gelöste Instanz.

Der Endpoint fängt das ab: Zielwerte oberhalb von 2^60 gelten als Sentinel, der Status
wird auf `INFEASIBLE` korrigiert, `objective_value` auf `null` gesetzt, und `note`
erklärt die Korrektur.

## Fallstrick: YasolSolver.jl schreibt stillschweigend unvollständige QLP-Dateien

Fehlen bei einer Variablen `lower_bound` und `upper_bound`, bricht YasolSolver.jl das
Schreiben der QLP-Datei nach der `BOUNDS`-Zeile ab — ohne Bounds-Einträge, ohne
`BINARIES`, ohne `EXISTS`/`ALL`, ohne `ORDER`:

```
MINIMIZE
+1.0x1 +1.0x2 ... + 0.0
SUBJECT TO
+4.0x1 +4.0x2 -3.0x3 >= 6.0
BOUNDS
```

Julia meldet dabei **Exit-Code 0**, es gibt keine Warnung. Erst Yasol scheitert:

```
terminate called after throwing an instance of 'utils::ParserException'
  what(): Exception while parsing: No order specified
```

`binary = true` allein genügt nicht, obwohl JuMP daraus 0/1-Bounds ableitet. Dieselbe
Modelldatei mit ergänzten `lower_bound`/`upper_bound` erzeugt eine vollständige Datei,
die Yasol löst. Wer YasolSolver.jl programmatisch nutzt, sollte Bounds also immer
explizit setzen — oder prüfen, ob die erzeugte Datei `ORDER` enthält.

## Bekannte Einschränkung: JuMP-Ergebniszugriff funktioniert nicht

`optimize!(model)` läuft durch und Yasol löst korrekt, aber `termination_status(model)`
liefert `MathOptInterface.OTHER_ERROR`, und `value(x)` / `objective_value(model)` sind
nicht nutzbar.

Ursache ist eine Formatabweichung zwischen den beiden Upstream-Projekten. Yasol schreibt
die Felder von `<quality>` heute als **Textinhalt**:

```xml
 <quality>
   SolutionStatus="OPTIMAL"
   Gap="0.000000"
 </quality>
```

`YasolSolver.importSolution` liest sie dagegen als **XML-Attribute** (`node["SolutionStatus"]`).
Direkt nachgemessen im Container:

```
importSolution FAILED: KeyError: key "SolutionStatus" not found
```

`MOI.optimize!` fängt diesen Fehler in einem `try`/`catch` ohne Behandlung ab
(`# TODO show error in results`), weshalb er nach außen nur als `OTHER_ERROR` sichtbar wird.

Praktische Folge: Die Lösung geht nicht verloren. Der Service parst die `.sol`-Datei
selbst und liefert sie im Feld `solutions` — inklusive Status, Zielfunktionswert und
Variablenbelegung. Wer die Werte in Julia braucht, liest die Datei im eigenen Code, statt
über die JuMP-Accessoren zu gehen.

Zweite Abweichung derselben Art: YasolSolver.jl übergibt `output info` und `time limit`
als **positionale** Argumente (`yasol datei.qlp 1 60`). Das aktuelle Yasol erwartet
`--name=wert` und ignoriert positionale Extra-Argumente. Das Zeitlimit aus
`set_optimizer_attribute(model, "time limit", …)` wirkt daher nicht — das Zeitlimit des
Endpoints (`timeout`) greift trotzdem, es beendet den kompletten Prozessbaum hart.

## Aufbau des Images

Zwei Stages:

1. **builder** — klont HiGHS (`v1.15.1`) und baut es *in place*. Yasols
   `FindHighs.cmake` erwartet einen HiGHS-*Quellbaum* (Header in `src/` oder `highs/`,
   `HConfig.h` in `build/`, `libhighs` in `build/lib/`), kein installiertes Prefix.
   Danach Yasol mit `-DUSE_HIGHS=ON`.
2. **runtime** — Yasol-Binary, `libhighs.so`, Julia + vorkompiliertes
   YasolSolver.jl-Environment, FastAPI. Läuft als User `yasol` (uid 1000).

Image: ~1,2 GB. Build-Args: `HIGHS_REF`, `YASOL_REF`, `JULIA_VERSION`.

### Zwei Patches, die der Build braucht

**`-m64` auf ARM.** Yasols `CMakeLists.txt` setzt `-m64` fest; gcc kennt das Flag nur auf
x86. Auf allen anderen Architekturen wird es herausgesedet. Auf x86 ist es ohnehin der
Default, und die Quellen enthalten keine x86-Intrinsics — geprüft.

**Depot-Rechte.** Per Git-URL installierte Julia-Pakete werden mit Modus `0700`
ausgecheckt (Registry-Pakete dagegen mit `0755`). Ohne `chmod -R a+rX` kann der
Nicht-Root-Runtime-User YasolSolver nicht lesen; Julia meldet dann irreführend
"required but does not seem to be installed". Zusätzlich bekommt der Runtime-User ein
eigenes, beschreibbares Depot vorangestellt (`/home/yasol/.julia:/opt/julia-depot`), weil
Julia auch beim reinen Lesen Sperrdateien und Nutzungslogs schreiben will.

## Test

```bash
./scripts/smoke_test.sh http://127.0.0.1:8010
```

Prüft beide Endpoints gegen bekannte Werte: `knapsack.qlp` → `OPTIMAL`, Zielwert `863`;
Moore & Bard über Julia → `OPTIMAL`, Zielwert `-3`, `x1=1, x2=1, x3=0, x4=0`.

## Konfiguration

Alles über Environment-Variablen, Defaults in [api/settings.py](api/settings.py):

| Variable | Default | Bedeutung |
|---|---|---|
| `YASOL_MAX_TIMEOUT` | 600 | Obergrenze, die die API pro Request akzeptiert |
| `YASOL_DEFAULT_TIME_LIMIT` | 60 | Zeitlimit, wenn der Request keins setzt |
| `YASOL_TIMEOUT_GRACE` | 15 | Zuschlag für den harten Kill über Yasols `--timeLimit` |
| `YASOL_MAX_UPLOAD_BYTES` | 16 MiB | maximale Instanzgröße |
| `YASOL_MAX_OUTPUT_CHARS` | 256 KiB | Kürzungsgrenze für stdout/stderr/Artefakte |

Yasols Standardparameter stehen in [Yasol.ini](Yasol.ini) und werden in jedes
Arbeitsverzeichnis kopiert. `writeOutputFile=1` ist dort gesetzt — ohne das schreibt
Yasol beim Julia-Weg keine `.sol`-Datei, weil YasolSolver.jl kein `--outputFile=1` übergibt.
