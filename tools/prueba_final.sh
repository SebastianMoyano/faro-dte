#!/usr/bin/env bash
# tools/prueba_final.sh — LA prueba final: todos los gates del port, en un solo comando.
#
#   ./tools/prueba_final.sh            # todo (con red: algunos pasos del contrato consultan al SII)
#   ./tools/prueba_final.sh --sin-red  # sin tocar el SII (lo que corre la CI)
#   ./tools/prueba_final.sh --app      # además compila la app Wails (necesita `wails`)
#
# Gates, en orden:
#   1. corpus dorado: Python == esperado (15 casos)                     test_dorado.py
#   2. práctica sin red (Python)                                        test_simulacion.py
#   3. núcleo Go: vet + tests (xsd 1.070 vectores, pdf417, portal, rcv, rvd, sii, siiengine, servidor…)
#   4. corpus dorado: Go == esperado, byte a byte (port externo)        test_dorado.py --port
#   5. esquema dorado: la base que crea Go == la que crea Python        tools/esquema_dorado.py
#   6. contrato dorado: los dos motores sobre una COPIA de la base     tools/contrato_api.py todos
#   6b. el MCP en Go por stdio (tools/list y salud sobre una base vacía)
#   7. la app: vet + test (el motor Go en proceso sobre una carpeta de datos vacía) [+ wails build]
#   8. motor Rust (fase 8, si hay cargo): esquema dorado + contrato dorado (Python vs Rust) + MCP por
#      stdio: el servidor Rust responde lo mismo que Python en las 94 rutas
#   6c. público: ni RUTs reales, personas, correos, secretos ni rutas (árbol + historia + .gitignore),
#       y los gates del gate (--autoprueba de auditar_publico y auditar_release)
#   9. la app Tauri (app-rs, si hay cargo): clippy + test (motor Rust en proceso sobre una carpeta
#      vacía; la app como MCP) [+ cargo tauri build con --app]
# Sale 0 solo si TODO pasa. Nada toca la base real: el contrato corre sobre una copia temporal.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
SIN_RED=""; APP=0
for a in "$@"; do case "$a" in --sin-red) SIN_RED="--sin-red";; --app) APP=1;; esac; done
TMP="$(mktemp -d)"; trap 'kill $(cat "$TMP"/*.pid 2>/dev/null) 2>/dev/null; rm -rf "$TMP"' EXIT
fallas=0; resumen=()
gate() { # gate "nombre" comando…
  local nombre="$1"; shift
  local ini=$SECONDS
  if "$@" >"$TMP/$nombre.log" 2>&1; then
    resumen+=("✓ $nombre ($((SECONDS-ini)) s)")
  else
    resumen+=("✗ $nombre ($((SECONDS-ini)) s) → $TMP/$nombre.log"); fallas=$((fallas+1))
    tail -25 "$TMP/$nombre.log" | sed 's/^/    /'
    cp "$TMP/$nombre.log" "/tmp/prueba_final_$nombre.log"
  fi
}

echo "== 1. corpus dorado (Python)"; gate corpus_python "$PY" test_dorado.py
echo "== 2. práctica sin red (Python)"; gate practica_python "$PY" test_simulacion.py
echo "== 3. núcleo Go: vet + tests"; gate go_vet bash -c 'cd nucleo && go vet ./...'; gate go_test bash -c 'cd nucleo && go test ./...'
echo "== 4. corpus dorado (Go, port externo)"
gate go_build bash -c 'cd nucleo && go build -o bin/nucleo-dorado ./cmd/nucleo-dorado && go build -o bin/nucleo-servidor ./cmd/nucleo-servidor'
gate corpus_go "$PY" test_dorado.py --port nucleo/bin/nucleo-dorado
if command -v cargo >/dev/null 2>&1; then
  echo "== 4b. núcleo Rust (fase 8): tests + clippy + corpus"
  gate rust_test bash -c 'cd nucleo-rs && cargo test --quiet 2>&1 | tail -3'
  gate rust_clippy bash -c 'cd nucleo-rs && cargo clippy --release --all-targets --quiet -- -D warnings'
  gate rust_build bash -c 'cd nucleo-rs && cargo build --release --quiet'
  gate corpus_rust "$PY" test_dorado.py --port nucleo-rs/target/release/nucleo-dorado
else
  echo "== 4b. núcleo Rust: sin cargo en este equipo, se omite (CI sí lo corre)"
fi
echo "== 5. esquema dorado"; gate esquema "$PY" tools/esquema_dorado.py --go nucleo/bin/nucleo-servidor
echo "== 6. contrato dorado (dos motores, copia de la base)"
puerto_libre() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }
contrato() { # contrato <binario del motor portado>  (Go o Rust: el mismo contrato contra Python)
  local BIN="${1:-nucleo/bin/nucleo-servidor}"
  # fuera el -wal/-shm de la corrida anterior (Go antes que Rust): con una base nueva encima,
  # SQLite los mezclaba y respondía "database disk image is malformed"
  rm -rf "$TMP"/db.sqlite* "$TMP/storage"
  cp dte_database.db "$TMP/db.sqlite" && mkdir -p "$TMP/storage"
  local K; K="$(openssl rand -hex 16)"
  # puertos libres, no fijos: un servidor ajeno en el puerto de siempre hacía fallar el gate con 404 en todo
  local PPY PGO; PPY="$(puerto_libre)"; PGO="$(puerto_libre)"
  APP_SHELL_KEY=$K DATABASE_URL="sqlite:///$TMP/db.sqlite" STORAGE_PATH="$TMP/storage" RVD_SCHEDULER_ACTIVO=false \
    "$PY" -m uvicorn main:app --port "$PPY" >"$TMP/py.log" 2>&1 & echo $! >"$TMP/py.pid"
  APP_SHELL_KEY=$K DATABASE_URL="sqlite:///$TMP/db.sqlite" STORAGE_PATH="$TMP/storage" RVD_SCHEDULER_ACTIVO=false \
    "$BIN" servir --puerto "$PGO" --db "$TMP/db.sqlite" --motor "http://127.0.0.1:$PPY" >"$TMP/go.log" 2>&1 & echo $! >"$TMP/go.pid"
  for i in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$PPY/" && curl -s -o /dev/null "http://127.0.0.1:$PGO/" && break; sleep 0.5; done
  APP_SHELL_KEY=$K "$PY" tools/contrato_api.py --python "http://127.0.0.1:$PPY" --go "http://127.0.0.1:$PGO" --escenario todos $SIN_RED
  local rc=$?
  kill "$(cat "$TMP/py.pid")" "$(cat "$TMP/go.pid")" 2>/dev/null; rm -f "$TMP"/*.pid
  # si falla, los logs de los dos servidores quedan junto al del gate (el $TMP se borra al salir)
  [ $rc -eq 0 ] || { cp "$TMP/py.log" /tmp/prueba_final_contrato_py.log; cp "$TMP/go.log" /tmp/prueba_final_contrato_motor.log; }
  return $rc
}
gate contrato contrato
echo "== 6b. MCP en Go (stdio, base vacía)"
mcp_stdio() { # mcp_stdio <binario nucleo-mcp>  (Go o Rust)
  local MCP="${1:-nucleo/bin/nucleo-mcp}"
  [ "$MCP" = nucleo/bin/nucleo-mcp ] && { (cd nucleo && go build -o bin/nucleo-mcp ./cmd/nucleo-mcp) || return 1; }
  rm -f "$TMP/mcp.db"
  local out
  out=$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"salud","arguments":{}}}' \
    | "$MCP" --env "$TMP/no.env" --db "$TMP/mcp.db" 2>/dev/null)
  echo "$out" | grep -q '"name":"emitir_dte"' && echo "$out" | grep -q 'dte-chile-mcp' || return 1
  # el instalador de clientes: escribe la entrada en un HOME de prueba y la vuelve a leer
  rm -rf "$TMP/home"; mkdir -p "$TMP/home/.cursor"
  HOME="$TMP/home" "$MCP" instalar --en todos --json | grep -q '"cliente":"cursor"' || return 1
  grep -q '"dte-chile"' "$TMP/home/.cursor/mcp.json" || return 1
  HOME="$TMP/home" "$MCP" clientes | grep -q '^cursor .*conectado'
}
gate mcp mcp_stdio
if command -v cargo >/dev/null 2>&1; then
  echo "== 8. motor Rust (fase 8): esquema + contrato (Python vs Rust) + MCP por stdio"
  gate esquema_rust "$PY" tools/esquema_dorado.py --go nucleo-rs/target/release/nucleo-servidor
  gate contrato_rust contrato nucleo-rs/target/release/nucleo-servidor
  gate mcp_rust mcp_stdio nucleo-rs/target/release/nucleo-mcp
fi
echo "== 6c. público: ni RUTs reales, ni personas, ni secretos (árbol + historia + .gitignore); y los gates del gate"
gate publico "$PY" tools/auditar_publico.py
gate publico_autoprueba "$PY" tools/auditar_publico.py --autoprueba
gate release_autoprueba "$PY" tools/auditar_release.py --autoprueba
echo "== 7. la app (motor Go en proceso)"
gate app_vet bash -c 'cd app && go vet .'; gate app_test bash -c 'cd app && go test -count=1 . && rm -f app'
if [ "$APP" = 1 ]; then gate app_wails bash -c 'cd app && PATH="$PATH:$HOME/go/bin" wails build -platform darwin/arm64 -s'; fi
if command -v cargo >/dev/null 2>&1; then
  echo "== 9. la app Tauri (motor Rust en proceso)"
  gate app_rs_clippy bash -c 'cd app-rs && cargo clippy --all-targets --quiet -- -D warnings'
  gate app_rs_test bash -c 'cd app-rs && cargo test --quiet 2>&1 | tail -3'
  if [ "$APP" = 1 ]; then gate app_rs_tauri bash -c 'cd app-rs && PATH="$PATH:$HOME/.cargo/bin" cargo tauri build --bundles app'; fi
fi

echo; echo "== resumen"; printf '%s\n' "${resumen[@]}"
if [ "$fallas" = 0 ]; then echo "TODO PASÓ"; else echo "$fallas gate(s) con fallas"; fi
exit $fallas
