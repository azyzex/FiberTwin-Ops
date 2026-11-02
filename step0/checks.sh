#!/usr/bin/env bash
# Step 0 checks: can this laptop run the lab? Run as root inside Debian.
# Prints one PASS/FAIL line per risk, then the details.
set -uo pipefail

pass() { printf '\033[1;32mPASS\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m  %s\n' "$*"; }
info() { printf '      %s\n' "$*"; }

echo "== Kernel / resources =="
info "kernel: $(uname -r)"
info "CPUs: $(nproc)"
free -m | sed 's/^/      /'
df -h / | sed 's/^/      /'

echo
echo "== 1. Falco needs BTF (modern eBPF driver) =="
if [ -r /sys/kernel/btf/vmlinux ]; then
  pass "BTF present: /sys/kernel/btf/vmlinux ($(stat -c %s /sys/kernel/btf/vmlinux) bytes)"
else
  fail "no /sys/kernel/btf/vmlinux -> Falco modern eBPF will not work on this kernel"
fi

echo
echo "== 2. tc netem + htb (fault injection + scaled capacities) =="
ip link del ft0 2>/dev/null || true
ip link add ft0 type veth peer name ft1
ip link set ft0 up; ip link set ft1 up
if tc qdisc add dev ft0 root netem delay 10ms loss 1% 2>/tmp/netem.err; then
  pass "netem works: $(tc qdisc show dev ft0 | head -n1)"
else
  fail "netem: $(cat /tmp/netem.err)"
fi
tc qdisc del dev ft0 root 2>/dev/null || true
if tc qdisc add dev ft0 root handle 1: htb default 10 2>/tmp/htb.err && \
   tc class add dev ft0 parent 1: classid 1:10 htb rate 10mbit 2>>/tmp/htb.err; then
  pass "htb works: $(tc class show dev ft0 | head -n1)"
else
  fail "htb: $(cat /tmp/htb.err)"
fi
ip link del ft0 2>/dev/null || true

echo
echo "== 3. Docker Engine =="
if docker run --rm hello-world >/tmp/hello.out 2>&1; then
  pass "docker runs containers ($(docker --version))"
else
  fail "docker: $(tail -n 3 /tmp/hello.out)"
fi

echo
echo "== 4. containerlab =="
if command -v containerlab >/dev/null; then
  pass "containerlab $(containerlab version 2>/dev/null | awk '/version:/{print $2; exit}')"
else
  fail "containerlab not installed"
fi
