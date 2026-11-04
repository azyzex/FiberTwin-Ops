# Lab host: customers, the "Internet" server, ONTs, the OLT, the splitter and the attacker.
# 0.2 (Slice 3): + openssh (CPE management), nmap + hydra (attack tools for the security scenarios),
#                nftables (blocks pushed by the GitOps pipeline)
FROM alpine:3.22
RUN apk add --no-cache iperf3 iputils iproute2 tcpdump bash coreutils openssh nmap nmap-scripts hydra nftables
CMD ["sleep", "infinity"]
