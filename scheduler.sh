#!/bin/bash
# Hermes Nexus Scheduler
# 同時啟動所有 Agent，各自獨立在背景輪詢 Linear

PYTHON="venv/bin/python"
PIDS_DIR=".pids"
LOG_DIR="logs"

start_agent() {
    local name=$1
    local script=$2
    local interval=${3:-30}

    mkdir -p "$PIDS_DIR"
    "$PYTHON" "$script" --daemon --interval "$interval" >> "$LOG_DIR/${name}.log" 2>&1 &
    local pid=$!
    echo $pid > "$PIDS_DIR/${name}.pid"
    echo "  ✓ $name started (PID $pid)"
}

stop_all() {
    echo "Stopping all agents..."
    for pid_file in "$PIDS_DIR"/*.pid; do
        [ -f "$pid_file" ] || continue
        local name
        name=$(basename "$pid_file" .pid)
        local pid
        pid=$(cat "$pid_file")
        if kill "$pid" 2>/dev/null; then
            echo "  ✓ $name stopped (PID $pid)"
        else
            echo "  - $name already stopped"
        fi
        rm -f "$pid_file"
    done
}

status_all() {
    local found=0
    for pid_file in "$PIDS_DIR"/*.pid 2>/dev/null; do
        [ -f "$pid_file" ] || continue
        found=1
        local name
        name=$(basename "$pid_file" .pid)
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  ● $name  running  (PID $pid)"
        else
            echo "  ✗ $name  dead     (PID $pid)"
            rm -f "$pid_file"
        fi
    done
    [ $found -eq 0 ] && echo "  No agents running."
}

case "${1:-start}" in
    start)
        echo "Starting Hermes Nexus agents..."
        mkdir -p "$LOG_DIR"
        start_agent "master"    "agents/hermes_master/hermes_master.py"          30
        start_agent "dev"       "agents/development_agent/development_agent.py"  30
        start_agent "doc"       "agents/document_agent/document_agent.py"        30
        start_agent "ppt"       "agents/presentation_agent/presentation_agent.py" 60
        start_agent "reviewer"  "agents/reviewer_agent/reviewer_agent.py"        30
        echo ""
        echo "All agents running. Logs: logs/<name>.log"
        echo "Status:  ./scheduler.sh status"
        echo "Stop:    ./scheduler.sh stop"
        ;;
    stop)
        stop_all
        ;;
    status)
        echo "Agent status:"
        status_all
        ;;
    restart)
        stop_all
        sleep 2
        "$0" start
        ;;
    *)
        echo "Usage: ./scheduler.sh [start|stop|status|restart]"
        exit 1
        ;;
esac
