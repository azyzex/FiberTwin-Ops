#!/usr/bin/env bash
# Slice 1f checkpoint on the laptop: a connector on ONT3's drop degrades; the system must name the DROP of
# ont3 (not the feeder) in a work order before the ONT goes dark, and a "repaired" report must only close
# the incident when the measurements agree. Writes results/1f-access-e2e.json.
set -uo pipefail
cd "$(dirname "$0")/.."
AGENT=http://localhost:8081; API=http://localhost:8000; N8N=http://localhost:5678; PROM=http://localhost:9090
T0=$(date +%s)
el() { echo $(( $(date +%s) - T0 )); }
margin() { curl -s "$PROM/api/v1/query" --data-urlencode "query=min(ft_pon_margin_db{ont=\"$1\"})" | jq -r '.data.result[0].value[1] // "nan"' | cut -c1-5; }
reachable() { docker exec clab-ft-core-srv ping -c 2 -W 1 -q "$1" >/dev/null 2>&1 && echo yes || echo no; }
form() {  # the technician's form, submitted like a browser would
  curl -s -o /dev/null -w '%{http_code}' -X POST "$N8N/form/fibertwin-repaired" -F "field-0=$1" -F "field-1=$2"
}
declare -A M

curl -s -X DELETE "$AGENT/faults" >/dev/null; sleep 40
echo "start: ont3 margin $(margin ont3) dB, reachable $(reachable 100.64.10.13)"
curl -s -X POST "$AGENT/fault/pon-ramp" -H 'Content-Type: application/json' \
     -d '{"segment":"drop:ont3","to_db":10,"db_per_min":3}' >/dev/null
INC=""
while [ "$(el)" -lt 420 ]; do
  [ -z "${M[alert_s]:-}" ] && curl -s "$PROM/api/v1/query" --data-urlencode 'query=ALERTS{alertname=~"FtPon.*",alertstate="firing"}' \
     | jq -e '.data.result | length > 0' >/dev/null && { M[alert_s]=$(el); M[alert_margin]=$(margin ont3); echo "t=$(el)s PON alert firing (ont3 margin ${M[alert_margin]} dB)"; }
  if [ -z "$INC" ]; then
    j=$(curl -s "$API/incidents" | jq -c '[.[] | select(.fault_class=="F3" and .issue_url != null and .created > '"$T0"')][0] // empty')
    if [ -n "$j" ]; then
      INC=$(echo "$j" | jq -r .id); M[incident]=$INC; M[segment]=$(echo "$j" | jq -r .fibre); M[issue]=$(echo "$j" | jq -r .issue_url)
      M[work_order_s]=$(el); M[work_order_margin]=$(margin ont3); M[ont3_reachable_at_work_order]=$(reachable 100.64.10.13)
      echo "t=$(el)s WORK ORDER $INC for ${M[segment]}: ${M[issue]} (ont3 margin ${M[work_order_margin]} dB, still reachable: ${M[ont3_reachable_at_work_order]})"
    fi
  fi
  [ -z "${M[dark_s]:-}" ] && [ "$(reachable 100.64.10.13)" = no ] && { M[dark_s]=$(el); echo "t=$(el)s ont3 stops answering"; }
  [ -n "$INC" ] && [ -n "${M[dark_s]:-}" ] && break
  sleep 5
done
M[others_reachable]=$(for ip in 100.64.10.11 100.64.10.12 100.64.10.14; do reachable $ip; done | sort -u | tr '\n' ' ')
echo "other ONTs reachable: ${M[others_reachable]}"

echo "technician reports a repair TOO EARLY (fault still there):"
M[early_form_http]=$(form "$INC" "cleaned the connector (not really)"); sleep 5
M[early_state]=$(curl -s "$API/incidents/$INC" | jq -r .state)
echo "  form HTTP ${M[early_form_http]} -> incident state: ${M[early_state]}"

echo "real repair:"
curl -s -X DELETE "$AGENT/faults" >/dev/null; sleep 45
echo "  ont3 margin $(margin ont3) dB, reachable $(reachable 100.64.10.13)"
M[real_form_http]=$(form "$INC" "cleaned and re-mated the SC/APC connector at the splitter port"); sleep 5
M[final_state]=$(curl -s "$API/incidents/$INC" | jq -r .state)
M[issue_state]=$(curl -s "https://api.github.com/repos/azyzex/FiberTwin-Ops/issues/${M[issue]##*/}" | jq -r .state)
echo "  form HTTP ${M[real_form_http]} -> incident ${M[final_state]}, GitHub issue ${M[issue_state]}"
M[events]=$(curl -s "$API/incidents/$INC/events" | jq -c '[.[] | {type, summary: (.summary[0:120])}]')

mkdir -p results
{ echo "{"; for k in "${!M[@]}"; do v=${M[$k]}; if [ "$k" = events ]; then echo "  \"$k\": $v,"; else echo "  \"$k\": \"$v\","; fi; done; echo "  \"date\": \"$(date -u +%FT%TZ)\"}"; } > results/1f-access-e2e.json
jq . results/1f-access-e2e.json >/dev/null && echo "-> results/1f-access-e2e.json"
