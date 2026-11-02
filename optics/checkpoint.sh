#!/usr/bin/env bash
# Slice 1d checkpoint: gray failure of fibre F12 (loss growing inside span 0), then repair.
# Logs every 5 s to results/1d-ramp.csv: injected loss (truth), measured OSNR margin and measured link loss
# for every link, OSPF/BFD on r1, and the alerts firing. Expected: margins fall on L12 + L13 only (SRLG),
# alerts fire on the margin TREND well before any loss, loss appears only past the cliff.
set -uo pipefail
cd "$(dirname "$0")/.."
PROM=${PROM:-http://localhost:9090}; API=${API:-http://localhost:8081}
TO_DB=${TO_DB:-32}; RATE=${RATE:-6}       # dB per minute (time-compressed degradation)
HOLD=${HOLD:-40}
mkdir -p results
OUT=results/1d-ramp.csv
q() { curl -s "$PROM/api/v1/query" --data-urlencode "query=$1"; }
val() { q "$1" | jq -r --arg l "$2" '[.data.result[] | select(.metric.link==$l) | .value[1]][0] // "nan"'; }
alerts() { q 'ALERTS{alertstate="firing"}' | jq -r '[.data.result[].metric | .alertname + ":" + (.link // .node // ((.src // "") + ">" + (.dst // "")))] | sort | unique | join(" ")'; }
r1() { q "$1{node=\"r1\"}" | jq -r '.data.result[0].value[1] // "nan"'; }
LINKS="L12 L13 L23 L34 L41"
hdr="t_s,truth_extra_db"; for l in $LINKS; do hdr="$hdr,margin_$l,loss_$l"; done
echo "$hdr,r1_ospf_full,r1_bfd_up,alerts" > "$OUT"

curl -s -X DELETE "$API/faults" >/dev/null
sleep 10
T0=$(date +%s)
curl -s -X POST "$API/fault/ramp" -d "{\"fibre\":\"F12\",\"span\":0,\"to_db\":$TO_DB,\"db_per_min\":$RATE}" >/dev/null
DUR=$(( TO_DB * 60 / RATE + HOLD ))
echo "ramping F12/span0 to +$TO_DB dB at $RATE dB/min, then holding ${HOLD}s (~${DUR}s total)"
while [ $(( $(date +%s) - T0 )) -le "$DUR" ]; do
  t=$(( $(date +%s) - T0 ))
  truth=$(q 'ft_truth_span_extra_loss_db{fibre="F12",span="0"}' | jq -r '.data.result[0].value[1] // "nan"')
  row="$t,$truth"
  for l in $LINKS; do row="$row,$(val ft_optical_margin_db "$l"),$(val ft:link_loss_ratio "$l")"; done
  echo "$row,$(r1 ft_ospf_full_neighbors),$(r1 ft_bfd_up),$(alerts)" >> "$OUT"
  sleep 5
done
echo "repair at t=$(( $(date +%s) - T0 ))s"
curl -s -X DELETE "$API/faults" >/dev/null
sleep 40
t=$(( $(date +%s) - T0 )); row="$t,0"; for l in $LINKS; do row="$row,$(val ft_optical_margin_db "$l"),$(val ft:link_loss_ratio "$l")"; done
echo "$row,$(r1 ft_ospf_full_neighbors),$(r1 ft_bfd_up),$(alerts)" >> "$OUT"
echo "done -> $OUT"
