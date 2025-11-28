Nice — below is the **complete, ready-to-copy** scheduler script (I finished the part that was cut off). It will **create the `logs` folder** itself, so you don't need to manually create it. It uses the Python binary we found (`/bin/mf_24.9-2-0/bin/python3`) and runs both scripts on **weekdays** at **10:00** and **17:00**.

Save as `~/projects/myjob/run_scheduler_weekdays.sh` (or replace `WORKDIR` if your project path is different), then `chmod +x` and run with `nohup`.

---

### Full script — copy everything below

```bash
#!/usr/bin/env bash
set -euo pipefail

# ---------------- CONFIG ----------------
WORKDIR="$HOME/PROJECT-DATAVIZ/code/jithu"   # <-- change if your project path is different
PY="/bin/mf_24.9-2-0/bin/python3"           # exact python path from your environment
AUTO_MAIL_SCRIPT="$WORKDIR/auto_mail.py"
BACKLOG_SCRIPT="$WORKDIR/backlog.py"
TIMES=("10:00" "17:00")                     # times (HH:MM 24h) to run each weekday
LOGDIR="$WORKDIR/logs"
LOCKDIR="/tmp/myjob_locks"
HEARTBEAT_LOG="$LOGDIR/heartbeat.log"
AUTO_LOG="$LOGDIR/auto_mail.log"
BACKLOG_LOG="$LOGDIR/backlog.log"
SLEEP_MARGIN=5                              # small margin to avoid duplicate runs
# -----------------------------------------

# Ensure directories exist
mkdir -p "$LOGDIR"
mkdir -p "$LOCKDIR"

timestamp() { date -Iseconds; }

# Atomic lock using mkdir
with_lock() {
  local lockname="$1"; shift
  local lockpath="$LOCKDIR/$lockname"
  if mkdir "$lockpath" 2>/dev/null; then
    trap "rmdir '$lockpath' >/dev/null 2>&1 || true" EXIT
    "$@"
    rmdir "$lockpath" >/dev/null 2>&1 || true
    trap - EXIT
    return 0
  else
    echo "[$(timestamp)] Lock $lockname present; skipping." >> "$LOGDIR/locks.log"
    return 1
  fi
}

run_auto_mail() {
  echo "[$(timestamp)] START auto-mail" >> "$AUTO_LOG"
  with_lock "auto_mail" "$PY" "$AUTO_MAIL_SCRIPT" >> "$AUTO_LOG" 2>&1 \
    || echo "[$(timestamp)] auto-mail skipped due to existing lock" >> "$AUTO_LOG"
  echo "[$(timestamp)] END auto-mail" >> "$AUTO_LOG"
}

run_backlog() {
  echo "[$(timestamp)] START backlog" >> "$BACKLOG_LOG"
  with_lock "backlog" "$PY" "$BACKLOG_SCRIPT" >> "$BACKLOG_LOG" 2>&1 \
    || echo "[$(timestamp)] backlog skipped due to existing lock" >> "$BACKLOG_LOG"
  echo "[$(timestamp)] END backlog" >> "$BACKLOG_LOG"
}

is_weekday() {
  local dow
  dow=$(date +%u)   # 1..7 (Mon..Sun)
  if [ "$dow" -ge 1 ] && [ "$dow" -le 5 ]; then
    return 0
  else
    return 1
  fi
}

# Compute next time for a given HH:MM, guaranteed to be a weekday
next_epoch_for_time() {
  local hhmm="$1"
  local now candidate i dow
  now=$(date +%s)
  candidate=$(date -d "$(date +%F) $hhmm" +%s 2>/dev/null || date -d "tomorrow $hhmm" +%s)
  if [ "$candidate" -le "$now" ]; then
    candidate=$(date -d "tomorrow $hhmm" +%s)
  fi
  i=0
  while true; do
    dow=$(date -d "@$candidate" +%u)
    if [ "$dow" -ge 1 ] && [ "$dow" -le 5 ]; then
      echo "$candidate"
      return 0
    fi
    i=$((i+1))
    candidate=$(date -d "today +$i day $hhmm" +%s)
    if [ "$i" -gt 14 ]; then
      echo "$candidate"
      return 0
    fi
  done
}

echo "[$(timestamp)] Scheduler started. Using python: $PY. Times: ${TIMES[*]}" >> "$HEARTBEAT_LOG"

# Main loop
while true; do
  now=$(date +%s)

  # find nearest next scheduled time among TIMES
  next_time=0
  for t in "${TIMES[@]}"; do
    epoch=$(next_epoch_for_time "$t")
    if [ "$next_time" -eq 0 ] || [ "$epoch" -lt "$next_time" ]; then
      next_time="$epoch"
    fi
  done

  sleep_sec=$((next_time - now))
  if [ "$sleep_sec" -le 0 ]; then sleep_sec=1; fi

  echo "[$(timestamp)] Next run at $(date -d "@$next_time" --iso-8601=seconds) (sleeping $sleep_sec s)" >> "$HEARTBEAT_LOG"
  sleep "$sleep_sec"

  current_hm=$(date +%H:%M)

  if is_weekday; then
    for t in "${TIMES[@]}"; do
      if [ "$current_hm" = "$t" ]; then
        echo "[$(timestamp)] Matched $t — launching jobs" >> "$HEARTBEAT_LOG"
        # run both (each has its own lock)
        run_auto_mail &
        run_backlog &
        wait
        echo "[$(timestamp)] Jobs completed for $t" >> "$HEARTBEAT_LOG"
        break
      fi
    done
  else
    echo "[$(timestamp)] Wake at $current_hm but weekend — skipping" >> "$HEARTBEAT_LOG"
  fi

  # small margin to avoid double runs inside same minute
  sleep "$SLEEP_MARGIN"
done
```

---

### How to install & run

1. Save the script (e.g. `nano ~/projects/myjob/run_scheduler_weekdays.sh`)
2. Make executable:

```bash
chmod +x ~/projects/myjob/run_scheduler_weekdays.sh
```

3. Start it in background (no tmux needed):

```bash
nohup ~/projects/myjob/run_scheduler_weekdays.sh >/dev/null 2>&1 &
```

4. Confirm it is running:

```bash
ps -ef | grep run_scheduler_weekdays.sh | grep -v grep
```

5. View logs:

```bash
tail -f ~/projects/myjob/logs/heartbeat.log
tail -f ~/projects/myjob/logs/auto_mail.log
tail -f ~/projects/myjob/logs/backlog.log
```

---

### Make it auto-start when you login (optional)

Add to `~/.bash_profile` (or `~/.profile`):

```bash
if ! pgrep -f "run_scheduler_weekdays.sh" >/dev/null 2>&1; then
  nohup "$HOME/projects/myjob/run_scheduler_weekdays.sh" >/dev/null 2>&1 &
fi
```

---

If your actual project path is **not** `"$HOME/PROJECT-DATAVIZ/code/jithu"`, tell me the exact path (or copy the `pwd` output from your project folder) and I’ll paste a version with `WORKDIR` prefilled. Also, if you want the two scripts to run sequentially (one after another) instead of in parallel, I can switch them to run sequentially. Which do you prefer?
