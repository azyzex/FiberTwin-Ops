#!/usr/bin/env bash
# Slice 1c checkpoint: inject 5 % loss on link L12 (both ends) and time what monitoring does.
#   1. the loss is visible in Prometheus (ft:link_loss_ratio)
#   2. exactly ONE alert fires (FtLinkPacketLoss for L12; path symptoms are inhibited)
#   3. the map turns that link red (ft:link_health = 2)
#   4. Slack got one notification; then the impairment is removed and the alert resolves
set -uo pipefail
P=clab-ft-core
PROM=${PROM:-http://localhost:9090}
AM=${AM:-http://localhost:9093}
LOSS=${LOSS:-5}
q() { curl -s "$PROM/api/v1/query" --data-urlencode "query=$1" | jq -r '.data.result[0].value[1] // empty'; }
qn() { curl -s "$PROM/api/v1/query" --data-urlencode "query=$1" | jq '.data.result | length'; }
# read Alertmanager directly (through Prometheus the counter lags one scrape)
slack_sent() { curl -s "$AM/metrics" | grep -F 'alertmanager_notifications_total{integration="slack"}' | awk '{print $2}'; }
netem() { for end in r1:eth1 r2:eth1; do
  docker exec "$P-${end%%:*}" tc qdisc change dev "${end##*:}" parent 1:10 handle 10: netem delay 700us ${1:+loss $1%} limit 10000; done; }
wait_for() { local t0=$1 expr=$2; while :; do v=$(eval "$expr"); [ -n "$v" ] && break; sleep 1; done; awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN{printf "%.1f", b-a}'; }

before=$(slack_sent)
echo "injecting ${LOSS}% loss on L12 (r1:eth1 + r2:eth1) at $(date -u +%T)"
T0=$(date +%s.%N); netem "$LOSS"
echo "  visible in Prometheus after   $(wait_for "$T0" "q 'ft:link_loss_ratio{link=\"L12\"} > 0.01'") s"
echo "  map red (link_health=2) after $(wait_for "$T0" "q 'ft:link_health{link=\"L12\"} == 2'") s"
echo "  alert FIRING after            $(wait_for "$T0" "q 'ALERTS{alertname=\"FtLinkPacketLoss\",link=\"L12\",alertstate=\"firing\"}'") s"
sleep 45   # let any path symptom alerts appear too, so we can count everything
echo "  firing alerts (Prometheus):   $(qn 'ALERTS{alertstate="firing"}')  -> $(curl -s "$PROM/api/v1/query" --data-urlencode 'query=ALERTS{alertstate="firing"}' | jq -r '[.data.result[].metric | .alertname + "/" + (.link // .src+"->"+.dst // "")] | join(", ")')"
echo "  notified (not inhibited) in Alertmanager: $(curl -s "$AM/api/v2/alerts?active=true&inhibited=false&silenced=false" | jq -r '[.[].labels | .alertname + "/" + (.link // "")] | join(", ")')"
echo "  Slack notifications sent:     $(awk -v a="$before" -v b="$(slack_sent)" 'BEGIN{print b-a}')"
echo "removing the impairment at $(date -u +%T)"
T1=$(date +%s.%N); netem ""
echo "  map green again after         $(wait_for "$T1" "q 'ft:link_health{link=\"L12\"} == 0'") s"
echo "  alert resolved after          $(wait_for "$T1" "[ \$(qn 'ALERTS{alertname=\"FtLinkPacketLoss\",alertstate=\"firing\"}') = 0 ] && echo y") s"
