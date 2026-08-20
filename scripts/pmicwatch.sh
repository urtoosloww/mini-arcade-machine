#!/bin/bash
# pmicwatch.sh -- log Pi 5 power rails at 5 Hz with fsync, so the last
# reading survives a hard power cut.
#
#     bash pmicwatch.sh          # log
#     bash pmicwatch.sh --show   # read back after a crash
#
# Note: this firmware exposes EXT5V_V (input volts) but NOT EXT5V_A, so
# total input current is not observable. A PMIC overcurrent trip is also
# faster than any sampling rate -- absence of sag does not rule it out.

LOG=/var/log/pmicwatch.log

if [ "${1:-}" = "--show" ]; then
    echo "=== last 30 samples ==="
    sudo tail -30 "$LOG"
    echo
    echo "=== minimum input voltage seen ==="
    sudo grep -o 'EXT5V=[0-9.]*' "$LOG" | cut -d= -f2 | sort -n | head -1
    echo "=== peak core current ==="
    sudo grep -o 'CORE_A=[0-9.]*' "$LOG" | cut -d= -f2 | sort -n | tail -1
    echo "=== peak 3V3 current ==="
    sudo grep -o '3V3_A=[0-9.]*' "$LOG" | cut -d= -f2 | sort -n | tail -1
    echo "=== samples in log ==="
    sudo wc -l < "$LOG"
    exit 0
fi

sudo touch "$LOG"; sudo chmod 666 "$LOG"
echo "Logging to $LOG at 5 Hz. Ctrl-C to stop."
echo "--- started $(date) ---" >> "$LOG"

while true; do
    A=$(vcgencmd pmic_read_adc 2>/dev/null)
    EXT=$(echo "$A" | grep -o 'EXT5V_V volt(24)=[0-9.]*' | cut -d= -f2)
    CORE=$(echo "$A" | grep -o 'VDD_CORE_A current(7)=[0-9.]*' | cut -d= -f2)
    V33=$(echo "$A"  | grep -o '3V3_SYS_A current(1)=[0-9.]*' | cut -d= -f2)
    V33V=$(echo "$A" | grep -o '3V3_SYS_V volt(9)=[0-9.]*' | cut -d= -f2)
    HDMI=$(echo "$A" | grep -o 'HDMI_A current(22)=[0-9.]*' | cut -d= -f2)
    T=$(vcgencmd measure_temp 2>/dev/null | cut -d= -f2)
    THR=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    printf "%s EXT5V=%.4f 3V3_V=%.4f 3V3_A=%.4f CORE_A=%.4f HDMI_A=%.4f T=%s THR=%s\n" \
        "$(date +%H:%M:%S.%2N)" "${EXT:-0}" "${V33V:-0}" "${V33:-0}" \
        "${CORE:-0}" "${HDMI:-0}" "$T" "$THR" >> "$LOG"
    sync
    sleep 0.2
done
